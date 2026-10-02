"""The `grepwise` command."""

import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import StrEnum
from typing import Annotated, Any

import psycopg
import typer
from pydantic import BaseModel, ValidationError
from rich.console import Console
from rich.table import Table

from grepwise.agent.llm import make_llm
from grepwise.agent.loop import run_agent
from grepwise.agent.runs import recent_runs
from grepwise.config import get_settings
from grepwise.db.connection import connect
from grepwise.db.migrate import migrate
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

app = typer.Typer(no_args_is_help=True, help="Grepwise: index code and ask cited questions.")
db_app = typer.Typer(no_args_is_help=True, help="Database commands.")
app.add_typer(db_app, name="db")
console = Console()


class SourceType(StrEnum):
    repo = "repo"
    docs = "docs"
    openapi = "openapi"


@app.callback()
def _setup(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING, format="%(levelname)s %(message)s"
    )
    # Our own progress messages (e.g. rate-limit pacing) always show.
    logging.getLogger("grepwise").setLevel(logging.INFO)
    # Never let HTTP client debug logs (which can include request headers) through.
    for noisy in ("httpx", "httpcore", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply pending migrations."""
    with connect(vectors=False) as conn:
        applied = migrate(conn)
    console.print(f"Applied: {', '.join(applied)}" if applied else "Database is up to date.")


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


def _run_index(run: Callable[[psycopg.Connection, Embedder], IndexSummary]) -> None:
    try:
        embedder = make_embedder(get_settings())
        with connect() as conn:
            summary = run(conn, embedder)
    except Exception as exc:
        console.print(f"[red]Indexing failed:[/red] {exc}")
        raise typer.Exit(1) from None
    _print_summary(summary)


def _print_summary(s: IndexSummary) -> None:
    sha = f" ({s.commit_sha[:12]})" if s.commit_sha else ""
    console.print(f"[green]Indexed[/green] {s.name}@{s.version}{sha}")
    skipped = ", ".join(f"{n} {reason}" for reason, n in sorted(s.skipped.items())) or "none"
    kinds = ", ".join(f"{n} {kind}" for kind, n in sorted(s.chunks_by_kind.items())) or "none"
    console.print(f"  documents:  {s.files_kept} kept; skipped: {skipped}")
    console.print(f"  chunks:     {s.chunks_by_kind.total()} ({kinds})")
    console.print(f"  embeddings: {s.embeddings_created} created, {s.embeddings_reused} reused")
    console.print(f"  time:       {s.seconds:.1f}s")


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
) -> None:
    """Hybrid search over everything indexed; shows which search found each result."""
    settings = get_settings()
    try:
        embedder = make_embedder(settings)
        with connect() as conn:
            results = search(
                conn, embedder, query, sources=source, kinds=kind, limit=limit,
                token_budget=budget,  # only citations are printed, so no budget by default
                candidates=settings.search_candidates,
            )  # fmt: skip
    except Exception as exc:
        console.print(f"[red]Search failed:[/red] {exc}")
        raise typer.Exit(1) from None
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
    with connect() as conn:
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
    with connect() as conn:
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
    with connect() as conn:
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


# --- Agent (M4) --------------------------------------------------------------------------


class Provider(StrEnum):
    gemini = "gemini"
    ollama = "ollama"


_STATUS_TEXT = {
    "not_found": "not found in the indexed sources",
    "bad_range": "line range is past the end of the file",
    "unseen": "real, but no tool showed it to the agent",
}


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Your question, in plain language")],
    source: Annotated[
        list[str] | None, typer.Option("--source", help="Only use these sources (repeatable)")
    ] = None,
    provider: Annotated[
        Provider | None, typer.Option("--provider", help="Override LLM_PROVIDER")
    ] = None,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Hide the tool steps")] = False,
) -> None:
    """Ask the agent a question; it calls tools, streams an answer, and checks citations."""
    settings = get_settings()
    llm = make_llm(settings.llm(provider.value if provider else None))
    failed = False
    with _tools() as ctx:
        for ev in run_agent(ctx, llm, question, sources=source):
            data = ev.data
            if ev.type == "step" and not quiet:
                args = json.dumps(data["args"], ensure_ascii=False)
                console.print(f"→ {data['tool']} {args}", style="cyan", markup=False)
            elif ev.type == "tool_result" and not quiet:
                style = "dim" if data["ok"] else "yellow"
                console.print(f"  {data['summary']}", style=style, markup=False)
            elif ev.type == "token":
                typer.echo(data["text"], nl=False)
            elif ev.type == "reset":
                typer.echo()
                console.print("(draft discarded: the agent is looking something up)", style="dim")
            elif ev.type == "citations":
                typer.echo()
                if data:
                    console.print("\nCitations", style="bold")
                for c in data:
                    if c["status"] == "verified":
                        console.print(f"  ✓ {c['citation']}", style="green", markup=False)
                    else:
                        reason = _STATUS_TEXT.get(c["status"], c["status"])
                        console.print(f"  ✗ {c['citation']}: {reason}", style="red", markup=False)
            elif ev.type == "done":
                grounded = "" if data["grounded"] else " · not grounded: no verified citations"
                console.print(
                    f"\n{data['steps']} tool calls · {data['input_tokens']:,} in / "
                    f"{data['output_tokens']:,} out tokens · {data['seconds']:.1f}s · "
                    f"{data['provider']}/{data['model']}{grounded}",
                    style="dim",
                    markup=False,
                )
            elif ev.type == "error":
                failed = True
                console.print(f"\n{data['code']}: {data['message']}", style="red", markup=False)
    if failed:
        raise typer.Exit(1)


@app.command()
def serve(
    port: Annotated[int, typer.Option("--port")] = 8000,
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
) -> None:
    """Run the HTTP API (POST /chat streams answers over SSE; GET /health)."""
    import uvicorn

    from grepwise.api.app import create_app

    uvicorn.run(create_app(), host=host, port=port)


@app.command()
def runs(limit: Annotated[int, typer.Option("--limit", min=1, max=200)] = 10) -> None:
    """Show recent agent runs."""
    with connect() as conn:
        rows = recent_runs(conn, limit)
    table = Table("id", "when", "question", "steps", "grounded", "tokens in/out", "time", "error")
    for r in rows:
        grounded = {True: "yes", False: "no", None: "-"}[r["grounded"]]
        table.add_row(
            str(r["id"]), r["created_at"].strftime("%m-%d %H:%M"), r["question"][:60],
            str(r["steps"]), grounded, f"{r['input_tokens'] or 0:,}/{r['output_tokens'] or 0:,}",
            f"{r['seconds'] or 0:.1f}s", r["error"] or "",
        )  # fmt: skip
    console.print(table)
