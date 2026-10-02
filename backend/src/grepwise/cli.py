"""The `grepwise` command."""

import logging
from collections.abc import Callable
from enum import StrEnum
from typing import Annotated

import psycopg
import typer
from rich.console import Console
from rich.table import Table

from grepwise.config import get_settings
from grepwise.db.connection import connect
from grepwise.db.migrate import migrate
from grepwise.ingest.embed import Embedder, make_embedder
from grepwise.ingest.pipeline import IndexSummary, index_docs, index_openapi, index_repo
from grepwise.ingest.store import database_size, source_stats
from grepwise.search.hybrid import SearchResult, search

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
) -> None:
    """Hybrid search over everything indexed; shows which search found each result."""
    settings = get_settings()
    try:
        embedder = make_embedder(settings)
        with connect() as conn:
            results = search(
                conn, embedder, query, sources=source, kinds=kind, limit=limit,
                token_budget=settings.search_token_budget,
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
