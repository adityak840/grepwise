"""`grepwise setup` and `grepwise doctor` (spec 14)."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
import typer
from rich.console import Console

from grepwise.config import get_settings
from grepwise.db.connection import SCHEMA_STEPS, connect
from grepwise.ingest.chunkers.code import GRAMMARS
from grepwise.ingest.embed import LocalEmbedder, make_embedder
from grepwise.worker import sources_missing_embeddings

console = Console()

Client = Literal["claude-code", "cursor", "claude-desktop"]
SERVER = "grepwise"


def config_path(client: Client, home: Path | None = None) -> Path:
    home = home or Path.home()
    if client == "claude-code":
        return home / ".claude.json"
    if client == "cursor":
        return home / ".cursor" / "mcp.json"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", home)) / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def server_command() -> list[str]:
    """The absolute path of the `grepwise` being run, plus `mcp`. A virtual environment's
    script runs without uv and keeps the client's working folder, so the project is found.
    The path isn't resolved: `uv tool upgrade` swaps what a ~/.local/bin link points to."""
    me = Path(sys.argv[0])
    if me.name.startswith("grepwise") and me.exists():
        return [str(me.absolute()), "mcp"]
    found = shutil.which("grepwise")
    return [found or "grepwise", "mcp"]


def server_entry(client: Client, command: list[str]) -> dict[str, Any]:
    entry: dict[str, Any] = {"command": command[0], "args": command[1:]}
    return {"type": "stdio", **entry, "env": {}} if client == "claude-code" else entry


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text() or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"{path} isn't a JSON object")
    return data


def _write(path: Path, data: dict[str, Any]) -> Path | None:
    """Write JSON atomically, after copying the old file to <name>.bak-grepwise."""
    backup = None
    if path.exists():
        backup = path.with_name(path.name + ".bak-grepwise")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-grepwise")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
    return backup


@dataclass
class Registration:
    path: Path
    replaced: bool
    backup: Path | None
    removed_project_entries: list[str]


def register(client: Client, command: list[str], home: Path | None = None) -> Registration:
    """Add (or replace) the user-level `grepwise` entry in the client's config, leaving every
    other server alone. For Claude Code, project-level `grepwise` entries are removed too:
    they would override this one in those projects (older setups used `--directory`)."""
    path = config_path(client, home)
    data = _load(path)
    servers = data.setdefault("mcpServers", {})
    replaced = SERVER in servers
    servers[SERVER] = server_entry(client, command)
    removed = []
    if client == "claude-code":
        for project, settings in (data.get("projects") or {}).items():
            if isinstance(settings, dict) and SERVER in (settings.get("mcpServers") or {}):
                del settings["mcpServers"][SERVER]
                removed.append(project)
    backup = _write(path, data)
    return Registration(path, replaced, backup, removed)


def _download_assets() -> None:
    import tree_sitter_language_pack as tslp

    settings = get_settings()
    console.print("Downloading the embedding model (once, about 130 MB)…")
    LocalEmbedder(settings.local_embedding_model, settings.embedding_dim,
                  cache_dir=settings.model_cache_dir).embed_query("warm up")  # fmt: skip
    missing = [g for g in GRAMMARS if g not in tslp.downloaded_languages()]
    if missing:
        console.print(f"Downloading tree-sitter grammars: {', '.join(missing)}…")
        tslp.download(missing)


def setup(
    client: Annotated[
        str, typer.Option("--client", help="claude-code, cursor or claude-desktop")
    ] = "claude-code",
    print_only: Annotated[
        bool, typer.Option("--print", help="Only print the config; change nothing")
    ] = False,
    skip_downloads: Annotated[
        bool, typer.Option("--skip-downloads", help="Don't download the model and grammars")
    ] = False,
) -> None:
    """Get Grepwise ready: database, embedding model, grammars, and the MCP server registered
    for every project in your editor."""
    if client not in ("claude-code", "cursor", "claude-desktop"):
        raise typer.BadParameter("use claude-code, cursor or claude-desktop", param_hint="--client")
    chosen: Client = client  # type: ignore[assignment]
    command = server_command()
    if print_only:
        typer.echo(json.dumps({"mcpServers": {SERVER: server_entry(chosen, command)}}, indent=2))
        return
    settings = get_settings()
    with closing(connect()):
        console.print(f"[green]✓[/green] Database: {settings.db_path}")
    if not skip_downloads:
        _download_assets()
        console.print("[green]✓[/green] Embedding model and grammars ready")
    claude = shutil.which("claude") if chosen == "claude-code" else None
    if claude:
        subprocess.run([claude, "mcp", "remove", "--scope", "user", SERVER], capture_output=True)
        result = subprocess.run(
            [claude, "mcp", "add", "--scope", "user", SERVER, "--", *command],
            capture_output=True, text=True,
        )  # fmt: skip
        if result.returncode != 0:
            raise typer.Exit(_fail(f"claude mcp add failed: {result.stderr.strip()}"))
        console.print(f"[green]✓[/green] Registered with Claude Code: {' '.join(command)}")
    else:
        reg = register(chosen, command)
        verb = "Replaced" if reg.replaced else "Added"
        console.print(f"[green]✓[/green] {verb} the grepwise server in {reg.path}")
        console.print(f"  command: {' '.join(command)}", style="dim", highlight=False)
        if reg.backup:
            console.print(f"  backup:  {reg.backup}", style="dim", highlight=False)
        for project in reg.removed_project_entries:
            console.print(f"  removed an older project-level entry for {project}", style="dim")
    console.print(
        "\nNext: restart your editor's sessions, then ask about a library you use. "
        "In a project, `grepwise sync` indexes all its dependencies up front."
    )


def _fail(message: str) -> int:
    console.print(f"[red]{message}[/red]", highlight=False)
    return 1


# --- doctor ------------------------------------------------------------------------------

Check = tuple[str, str, str]  # (status ✓ ! ✗, what, detail or fix)


def _folder_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


def run_checks(home: Path | None = None, pypi: Callable[[], bool] | None = None) -> list[Check]:
    settings = get_settings()
    checks: list[Check] = []
    try:
        mem = sqlite3.connect(":memory:")
        mem.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        checks.append(("✓", "SQLite with FTS5", sqlite3.sqlite_version))
    except sqlite3.OperationalError:
        checks.append(("✗", "SQLite with FTS5", "missing: install Grepwise with uv"))
    try:
        with closing(connect()) as conn:
            (version,) = conn.execute("PRAGMA user_version").fetchone()
            checks.append(
                ("✓", "Database", f"{settings.db_path} (schema {version}/{len(SCHEMA_STEPS)})")
            )
            rows = conn.execute("SELECT status, count(*) FROM sources GROUP BY status").fetchall()
            counts = dict(rows)
            model_id = make_embedder(settings).model_id  # names the model; doesn't load it
            embedding = len(sources_missing_embeddings(conn, model_id))
            total = sum(counts.values())
            detail = f"{total} indexed" + (f", {embedding} still embedding" if embedding else "")
            failed = counts.get("failed", 0)
            if failed:
                detail += f", {failed} failed (see grepwise sources)"
            checks.append(("!" if failed else "✓", "Sources", detail))
    except Exception as exc:
        checks.append(("✗", "Database", str(exc)))
    model_files = (
        list(settings.model_cache_dir.rglob("*.onnx")) if settings.model_cache_dir.exists() else []
    )
    checks.append(("✓", "Embedding model", str(settings.model_cache_dir)) if model_files
                  else ("✗", "Embedding model", "not downloaded: run grepwise setup"))  # fmt: skip
    try:
        import tree_sitter_language_pack as tslp

        missing = [g for g in GRAMMARS if g not in tslp.downloaded_languages()]
        hint = f"missing {', '.join(missing)}: fetched on first use, or run grepwise setup"
        checks.append(
            ("!", "Grammars", hint) if missing else ("✓", "Grammars", ", ".join(GRAMMARS))
        )
    except Exception as exc:
        checks.append(("!", "Grammars", str(exc)))
    git = shutil.which("git")
    checks.append(("✓", "git", git) if git else ("✗", "git", "not found: install git"))
    reachable = pypi() if pypi else _pypi_reachable()
    offline = "unreachable: indexing new libraries needs the network"
    checks.append(("✓", "PyPI", "reachable") if reachable else ("!", "PyPI", offline))
    checks += _registration_checks(home)
    megabytes = _folder_size(settings.home) / 2**20
    checks.append(("✓", "Disk", f"{settings.home}: {megabytes:.0f} MB"))
    return checks


def _pypi_reachable() -> bool:
    try:
        return httpx.head("https://pypi.org/simple/", timeout=5).status_code < 500
    except httpx.HTTPError:
        return False


def _registration_checks(home: Path | None) -> list[Check]:
    path = config_path("claude-code", home)
    try:
        data = _load(path)
    except (ValueError, json.JSONDecodeError) as exc:
        return [("!", "Claude Code", f"can't read {path}: {exc}")]
    entry = (data.get("mcpServers") or {}).get(SERVER)
    if not entry:
        return [
            ("✗", "Claude Code", "grepwise isn't registered for all projects: run grepwise setup")
        ]
    command = entry.get("command", "")
    args = entry.get("args", [])
    checks: list[Check] = [("✓", "Claude Code", f"registered: {command} {' '.join(args)}")]
    if "--directory" in args:
        checks.append(
            (
                "✗",
                "Claude Code",
                "uses --directory, so it can't see your project: run grepwise setup",
            )
        )
    if os.path.isabs(command) and not Path(command).exists():
        checks.append(("✗", "Claude Code", f"{command} no longer exists: run grepwise setup"))
    overrides = [p for p, s in (data.get("projects") or {}).items()
                 if isinstance(s, dict) and SERVER in (s.get("mcpServers") or {})]  # fmt: skip
    if overrides:
        checks.append(
            ("!", "Claude Code", f"project-level entries override it in: {', '.join(overrides)}")
        )
    return checks


def doctor() -> None:
    """Check that everything Grepwise needs is in place, with a fix for anything that isn't."""
    checks = run_checks()
    styles = {"✓": "green", "!": "yellow", "✗": "red"}
    for status, what, detail in checks:
        console.print(f"[{styles[status]}]{status}[/] {what:16} {detail}", highlight=False)
    if any(status == "✗" for status, _, _ in checks):
        raise typer.Exit(1)
