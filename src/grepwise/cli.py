"""The `grepwise` command."""

import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel, ValidationError
from rich.console import Console
from rich.table import Table

from grepwise import commands, evals, evals_answers, onboarding
from grepwise.config import REPO_ROOT, get_settings
from grepwise.db.connection import Conn, connect
from grepwise.ingest.embed import Embedder, make_embedder
from grepwise.ingest.pipeline import IndexSummary, index_docs, index_openapi, index_repo
from grepwise.ingest.store import database_size, source_stats
from grepwise.search.hybrid import SearchResult, search
from grepwise.tools import ToolContext, call_tool, describe_validation_error, serialize
from grepwise.tools.errors import ToolError
from grepwise.tools.find_symbol import FindSymbolInput, find_symbol
from grepwise.tools.get_endpoint import GetEndpointInput, get_endpoint
from grepwise.tools.read_file import ReadFileInput, read_file
from grepwise.tools.sources import ListSourcesInput, list_sources
from grepwise.tools.verify import verify_citation

app = typer.Typer(
    no_args_is_help=True, help="Grepwise: exact-version docs and code for your assistant."
)
eval_app = typer.Typer(no_args_is_help=True, help="Measure search quality.")
app.add_typer(eval_app, name="eval")
console = Console()

# Managing what's indexed (spec 12).
app.command()(commands.add)
app.command()(commands.deps)
app.command()(commands.sync)
app.command()(commands.remove)
app.command()(commands.gc)
# Install and onboarding (spec 14).
app.command()(onboarding.setup)
app.command()(onboarding.doctor)


class SourceType(StrEnum):
    repo = "repo"
    docs = "docs"
    openapi = "openapi"


class SearchMode(StrEnum):
    hybrid = "hybrid"
    vector = "vector"
    keyword = "keyword"


@app.callback()
def _setup(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING, format="%(levelname)s %(message)s"
    )
    # Our own progress messages (e.g. rate-limit pacing) always show.
    logging.getLogger("grepwise").setLevel(logging.INFO)
    # Never let HTTP client debug logs (which can include request headers) through.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@app.command()
def index(
    url: Annotated[str, typer.Argument(help="https://github.com/<owner>/<repo>")],
    ref: Annotated[str, typer.Option("--ref", help="Tag or branch to index")],
) -> None:
    """Clone, chunk, embed and store a GitHub repo's code at a tag or branch."""
    _run_index(lambda conn, embedder: index_repo(conn, embedder, url, ref))


@app.command("index-docs")
def index_docs_cmd(
    url: Annotated[str, typer.Argument(help="https://github.com/<owner>/<repo>")],
    ref: Annotated[str, typer.Option("--ref", help="Tag or branch to index")],
    path: Annotated[str, typer.Option("--path", help="Docs folder inside the repo, e.g. docs")],
    name: Annotated[
        str | None, typer.Option("--name", help="Source name (default: owner/repo)")
    ] = None,
) -> None:
    """Index the Markdown/MDX docs in one folder of a GitHub repo."""
    _run_index(lambda conn, embedder: index_docs(conn, embedder, url, ref, path, name=name))


@app.command("index-openapi")
def index_openapi_cmd(
    location: Annotated[str, typer.Argument(help="URL or file path of an OpenAPI 3.x spec")],
    name: Annotated[str, typer.Option("--name", help="Source name, e.g. petstore")],
    version: Annotated[str, typer.Option("--version", help="API version, e.g. 1.0.27")],
) -> None:
    """Index an OpenAPI 3.x spec (JSON or YAML): one endpoint per operation."""
    _run_index(lambda conn, embedder: index_openapi(conn, embedder, location, name, version))


def _run_index(run: Callable[[Conn, Embedder], IndexSummary]) -> None:
    try:
        embedder = make_embedder(get_settings())
        with closing(connect()) as conn:
            summary = run(conn, embedder)
    except Exception as exc:
        console.print(f"[red]Indexing failed:[/red] {exc}")
        raise typer.Exit(1) from None
    _print_summary(summary)


_print_summary = commands.print_summary


@app.command("search")
def search_cmd(
    query: Annotated[str, typer.Argument(help="A question or an exact name")],
    source: Annotated[
        list[str] | None, typer.Option("--source", help="name or name@version (repeatable)")
    ] = None,
    kind: Annotated[
        list[str] | None, typer.Option("--kind", help="function, section, endpoint... (repeatable)")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=50)] = 10,
    budget: Annotated[
        int | None,
        typer.Option("--budget", min=1, help="Stop before results exceed N estimated tokens"),
    ] = None,
    mode: Annotated[
        SearchMode, typer.Option("--mode", help="hybrid, or one side alone to compare")
    ] = SearchMode.hybrid,
    timing: Annotated[bool, typer.Option("--timing", help="Print how long each step took")] = False,
) -> None:
    """Hybrid search over everything indexed; shows which search found each result."""
    settings = get_settings()
    timings: dict[str, float] = {}
    try:
        embedder = make_embedder(settings)
        with closing(connect()) as conn:
            results = search(
                conn, embedder, query, sources=source, kinds=kind, limit=limit,
                token_budget=budget,  # only citations are printed, so no budget by default
                candidates=settings.search_candidates, mode=mode.value, timings=timings,
            )  # fmt: skip
    except Exception as exc:
        console.print(f"[red]Search failed:[/red] {exc}")
        raise typer.Exit(1) from None
    if timing:
        console.print("  ".join(f"{step} {ms:.1f} ms" for step, ms in timings.items()), style="dim")
    if not results:
        console.print("No results.")
        return
    table = Table("#", "citation", "kind", "symbol", "found by")
    for i, r in enumerate(results, 1):
        table.add_row(str(i), r.citation, r.kind, r.symbol or "", _found_by(r))
    console.print(table)


def _found_by(r: SearchResult) -> str:
    vec = f"vec #{r.vector_rank}" if r.vector_rank else "vec -"
    kw = f"kw #{r.keyword_rank}" if r.keyword_rank else "kw -"
    return f"{vec} / {kw}"


@app.command()
def stats(
    type_: Annotated[
        SourceType | None, typer.Option("--type", help="Only this source type")
    ] = None,
) -> None:
    """List indexed sources with status, chunk counts and database size."""
    with closing(connect()) as conn:
        rows = source_stats(conn, type_.value if type_ else None)
        size = database_size(conn)
    table = Table("source", "type", "version", "status", "documents", "chunks", "error")
    for r in rows:
        table.add_row(
            r.name, r.type, r.version, r.status, str(r.documents), str(r.chunks), r.error or ""
        )
    console.print(table)
    console.print(f"Database size: {size}")


# --- Tools (M3) --------------------------------------------------------------------------


@contextmanager
def _tools() -> Iterator[ToolContext]:
    with closing(connect()) as conn:
        yield ToolContext(conn, get_settings())


def _input[M: BaseModel](model: type[M], **fields: Any) -> M:
    """Build a tool's input model; invalid input becomes a one-line error, not a traceback."""
    try:
        return model(**fields)
    except ValidationError as exc:
        console.print("[red]bad_input:[/red] ", end="")
        console.print(describe_validation_error(exc), markup=False, highlight=False)
        raise typer.Exit(1) from None


def _fail(err: ToolError) -> None:
    console.print(f"[red]{err.error.code}:[/red] {err.error.message}", markup=True)
    console.print(err.error.hint, markup=False, style="dim")
    raise typer.Exit(1)


def _show(citation: str, content: str) -> None:
    console.print(citation, style="bold", markup=False, highlight=False)
    typer.echo(content.rstrip("\n"))  # raw text: never interpreted as markup


@app.command()
def sources(
    type_: Annotated[
        SourceType | None, typer.Option("--type", help="Only this source type")
    ] = None,
) -> None:
    """List indexed sources (the list_sources tool)."""
    with _tools() as ctx:
        out = list_sources(ctx, _input(ListSourcesInput, type=type_.value if type_ else None))
    if isinstance(out, ToolError):
        _fail(out)
        return
    table = Table("ref", "type", "status", "documents", "chunks", "subpath", "embedding model")
    for src in out.sources:
        table.add_row(
            src.ref, src.type, src.status, str(src.documents), str(src.chunks),
            src.subpath or "", src.embedding_model or "",
        )  # fmt: skip
    console.print(table)


@app.command()
def symbol(
    name: Annotated[str, typer.Argument(help="e.g. TimestampSigner, Signer.sign or sign")],
    source: Annotated[list[str] | None, typer.Option("--source", help="Repeatable")] = None,
    kind: Annotated[list[str] | None, typer.Option("--kind", help="Repeatable")] = None,
) -> None:
    """Find where a symbol is defined (the find_symbol tool)."""
    with _tools() as ctx:
        out = find_symbol(ctx, _input(FindSymbolInput, name=name, sources=source, kinds=kind))
    if isinstance(out, ToolError):
        _fail(out)
        return
    if not out.matches:
        console.print(f"No symbol named {name!r}.", markup=False)
        raise typer.Exit(1)
    if out.case_insensitive:
        console.print("No exact-case match; showing case-insensitive matches.", style="dim")
    table = Table("citation", "kind", "symbol", "signature")
    for m in out.matches:
        table.add_row(m.citation, m.kind, m.symbol, m.signature)
    console.print(table)


def _parse_lines(spec: str | None) -> tuple[int | None, int | None]:
    if spec is None:
        return None, None
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", spec.strip())
    if not match:
        raise typer.BadParameter("use START-END, e.g. 20-60", param_hint="--lines")
    start = int(match[1])
    return start, int(match[2]) if match[2] else start


@app.command()
def read(
    source: Annotated[str, typer.Argument(help="name@version (or name)")],
    path: Annotated[str, typer.Argument(help="File path, or an endpoint like 'GET /pet/{petId}'")],
    lines: Annotated[str | None, typer.Option("--lines", help="e.g. 20-60")] = None,
) -> None:
    """Print exact lines of an indexed file (the read_file tool)."""
    start, end = _parse_lines(lines)
    with _tools() as ctx:
        out = read_file(
            ctx, _input(ReadFileInput, source=source, path=path, start_line=start, end_line=end)
        )
    if isinstance(out, ToolError):
        _fail(out)
        return
    _show(out.citation, out.content)
    if out.truncated:
        console.print(
            f"… truncated; continue with --lines {out.next_start_line}-{out.total_lines}",
            style="dim",
        )


@app.command()
def endpoint(
    target: Annotated[str, typer.Argument(help="'GET /pet/42', '/pet/{petId}', ...")],
    source: Annotated[str | None, typer.Option("--source", help="name or name@version")] = None,
) -> None:
    """Show an API endpoint's full description (the get_endpoint tool)."""
    with _tools() as ctx:
        out = get_endpoint(ctx, _input(GetEndpointInput, path=target, source=source))
    if isinstance(out, ToolError):
        _fail(out)
        return
    for i, ep in enumerate(out.endpoints):
        if i:
            typer.echo()
        _show(ep.citation, ep.content)


@app.command()
def verify(
    citation: Annotated[str, typer.Argument(help="name@version:path:L<start>-L<end>")],
) -> None:
    """Check a citation against the database and print the cited lines."""
    with closing(connect()) as conn:
        out = verify_citation(conn, citation)
    if isinstance(out, ToolError):
        _fail(out)
        return
    console.print("valid", style="green")
    _show(out.citation, out.content)


@app.command()
def tool(
    name: Annotated[str, typer.Argument(help="list_sources, search, read_file, ...")],
    args: Annotated[str, typer.Argument(help='JSON arguments, e.g. \'{"query": "..."}\'')] = "{}",
) -> None:
    """Call a tool exactly as an agent would and print the raw JSON it gets back."""
    try:
        parsed = json.loads(args)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"not valid JSON: {exc}", param_hint="ARGS") from None
    with _tools() as ctx:
        result = call_tool(ctx, name, parsed)
    typer.echo(serialize(result))


# --- Evals ------------------------------------------------------------------------------


@eval_app.command("retrieval")
def eval_retrieval(
    mode: Annotated[SearchMode, typer.Option("--mode")] = SearchMode.hybrid,
    questions: Annotated[Path, typer.Option("--questions", help="The eval set")] = REPO_ROOT
    / "evals"
    / "questions.yaml",
    include_drafts: Annotated[bool, typer.Option("--include-drafts")] = False,
    out_dir: Annotated[
        Path, typer.Option("--out", help="Where to write retrieval-<mode>.json")
    ] = REPO_ROOT / "evals" / "results",
) -> None:
    """Hit@5 and MRR of search on the eval questions (offline, no LLM)."""
    settings = get_settings()
    qs = evals.load_questions(questions, include_drafts=include_drafts)
    embedder = make_embedder(settings)
    with closing(connect()) as conn:
        results = evals.run_retrieval(
            conn, embedder, qs, mode=mode.value, candidates=settings.search_candidates
        )
    summary = evals.summarize(results)
    table = Table("category", "questions", "Hit@5", "MRR", title=f"retrieval ({mode.value})")
    for name, m in sorted(summary.items(), key=lambda kv: (kv[0] == "all", kv[0])):
        table.add_row(name, str(int(m["n"])), f"{m['hit_at_5']:.2f}", f"{m['mrr']:.2f}")
    console.print(table)
    missed = [r.id for r in results if not r.hit]
    if missed:
        console.print(f"Not in the top {evals.HIT_AT}: {', '.join(missed)}", style="dim")
    console.print(f"Wrote {evals.write_report(results, mode.value, out_dir)}", style="dim")


@eval_app.command("resolution")
def eval_resolution(
    packages: Annotated[Path, typer.Option("--packages")] = REPO_ROOT / "evals" / "resolution.yaml",
) -> None:
    """Does each package resolve to the right GitHub repo and a tag for its version? (network)"""
    with closing(connect()) as conn:
        results = evals.run_resolution(conn, packages)
    table = Table("package", "version", "result", "repo / tag or error")
    for r in results:
        if r.ok:
            table.add_row(r.package, r.version, "[green]ok[/]", f"{r.repo} @ {r.tag}")
        elif r.repo is not None:
            table.add_row(
                r.package, r.version, "[red]wrong repo[/]", f"{r.repo} (expected {r.expected_repo})"
            )
        else:
            table.add_row(r.package, r.version, "[red]failed[/]", r.error or "")
    console.print(table)
    good = sum(r.ok for r in results)
    console.print(f"{good}/{len(results)} resolved to the right repo and an exact-version tag.")


@eval_app.command("answers")
def eval_answers(
    condition: Annotated[str, typer.Option("--condition", help="with, without, or both")] = "both",
    ids: Annotated[
        str | None, typer.Option("--ids", help="Only these question ids, comma-separated")
    ] = None,
    limit: Annotated[int | None, typer.Option("--limit", min=1, help="First N questions")] = None,
    model: Annotated[str, typer.Option("--model", help="Claude model alias")] = "sonnet",
    questions: Annotated[Path, typer.Option("--questions")] = REPO_ROOT
    / "evals"
    / "questions.yaml",
    out_dir: Annotated[Path, typer.Option("--out")] = REPO_ROOT / "evals" / "runs" / "answers",
) -> None:
    """Claude Code with vs. without Grepwise (headless `claude -p`; uses your Claude plan)."""
    if condition not in ("with", "without", "both"):
        raise typer.BadParameter("with, without or both", param_hint="--condition")
    conditions: list[evals_answers.Condition] = (
        ["without", "with"] if condition == "both" else [condition]  # type: ignore[list-item]
    )
    qs = evals_answers.load_answer_questions(questions)
    if ids:
        wanted = {i.strip() for i in ids.split(",")}
        qs = [q for q in qs if q.id in wanted]
    qs = qs[:limit] if limit else qs

    def show(r: evals_answers.AnswerResult) -> None:
        mark = (
            "error"
            if r.error
            else (
                f"facts {r.fact_coverage:.0%}, file {'yes' if r.path_hit else 'no'}"
                if r.honest is None
                else f"honest {'yes' if r.honest else 'no'}"
            )
        )
        used = ", ".join(
            f"{n.removeprefix('mcp__grepwise__')}:{k}" for n, k in (r.tools or {}).items()
        )
        console.print(f"{r.condition:7} {r.id:10} {mark}  ${r.cost_usd:.2f} {r.seconds:.0f}s "
                      f"{r.turns} turns  [{used}]", highlight=False)  # fmt: skip

    command = onboarding.server_command()
    results = evals_answers.run_answers(qs, conditions, out_dir, command, model, on_result=show)
    summary = evals_answers.summarize_answers(results)
    table = Table("", *summary, title=f"Claude Code ({model}) with vs. without Grepwise")
    for metric in ["questions", "fact_coverage", "path_hit", "honest_on_absent", "used_grepwise",
                   "used_web", "cost_usd", "seconds", "turns", "errors"]:  # fmt: skip
        table.add_row(metric, *(f"{summary[c][metric]:.2f}" for c in summary))
    console.print(table)
    report = REPO_ROOT / "evals" / "results" / "answers.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"model": model, "summary": summary}, indent=2) + "\n")


# --- MCP (M5) ----------------------------------------------------------------------------


@app.command()
def mcp(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Log INFO to stderr")] = False,
) -> None:
    """Run the MCP server over stdio, for Claude Code, Cursor or Claude Desktop."""
    from grepwise.mcp.stdio import serve_stdio

    # stdout carries the protocol: logs go to stderr only, quietly unless --verbose.
    logging.getLogger("grepwise").setLevel(logging.INFO if verbose else logging.WARNING)
    serve_stdio(get_settings())
