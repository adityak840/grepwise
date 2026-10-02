"""The `grepwise` command."""

import logging
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from grepwise.config import get_settings
from grepwise.db.connection import connect
from grepwise.db.migrate import migrate
from grepwise.ingest.embed import make_embedder
from grepwise.ingest.pipeline import IndexSummary, index_repo
from grepwise.ingest.store import database_size, source_stats

app = typer.Typer(no_args_is_help=True, help="Grepwise: index code and ask cited questions.")
db_app = typer.Typer(no_args_is_help=True, help="Database commands.")
app.add_typer(db_app, name="db")
console = Console()


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
    """Clone, chunk, embed and store a GitHub repo at a tag or branch."""
    settings = get_settings()
    try:
        embedder = make_embedder(settings)
        with connect() as conn:
            summary = index_repo(conn, embedder, url, ref)
    except Exception as exc:
        console.print(f"[red]Indexing failed:[/red] {exc}")
        raise typer.Exit(1) from None
    _print_summary(summary)


def _print_summary(s: IndexSummary) -> None:
    console.print(f"[green]Indexed[/green] {s.name}@{s.version} ({s.commit_sha[:12]})")
    skipped = ", ".join(f"{n} {reason}" for reason, n in sorted(s.skipped.items())) or "none"
    kinds = ", ".join(f"{n} {kind}" for kind, n in sorted(s.chunks_by_kind.items())) or "none"
    console.print(f"  files:      {s.files_kept} kept; skipped: {skipped}")
    console.print(f"  chunks:     {s.chunks_by_kind.total()} ({kinds})")
    console.print(f"  embeddings: {s.embeddings_created} created, {s.embeddings_reused} reused")
    console.print(f"  time:       {s.seconds:.1f}s")


@app.command()
def stats() -> None:
    """List indexed sources with status, chunk counts and database size."""
    with connect() as conn:
        rows = source_stats(conn)
        size = database_size(conn)
    table = Table("source", "type", "version", "status", "documents", "chunks", "error")
    for r in rows:
        table.add_row(
            r.name, r.type, r.version, r.status, str(r.documents), str(r.chunks), r.error or ""
        )
    console.print(table)
    console.print(f"Database size: {size}")
