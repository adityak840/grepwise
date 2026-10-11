"""grepwise setup, doctor and gc, plus last-used stamps (temp home; never the real config)."""

import json
import shutil
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from grepwise import cli, commands, onboarding
from grepwise.config import get_settings
from grepwise.db.connection import Conn, connect
from grepwise.ingest.embed import FakeEmbedder
from grepwise.ingest.pipeline import index_openapi
from grepwise.onboarding import config_path, register, run_checks
from grepwise.tools import ToolContext, call_tool
from tests.helpers import PETSTORE

COMMAND = ["/opt/bin/grepwise", "mcp"]


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fake user home (for editor configs) and Grepwise home (for the database)."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("GREPWISE_HOME", str(tmp_path / ".grepwise"))
    monkeypatch.setenv("GREPWISE_EMBEDDER", "fake")
    get_settings.cache_clear()
    for module in (cli, commands, onboarding):
        monkeypatch.setattr(module, "console", Console(width=300))
    yield tmp_path
    get_settings.cache_clear()


def test_register_claude_code_keeps_everything_else(home: Path) -> None:
    path = home / ".claude.json"
    path.write_text(json.dumps({
        "numStartups": 7,
        "mcpServers": {"other": {"command": "x"}, "grepwise": {"command": "old"}},
        "projects": {"/code/a": {"mcpServers": {"grepwise": {"args": ["--directory", "b"]},
                                                "keep": {}}}},
    }))  # fmt: skip
    reg = register("claude-code", COMMAND, home)
    data = json.loads(path.read_text())
    assert reg.replaced and reg.removed_project_entries == ["/code/a"]
    assert data["numStartups"] == 7 and data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["grepwise"] == {
        "type": "stdio", "command": "/opt/bin/grepwise", "args": ["mcp"], "env": {},
    }  # fmt: skip
    assert data["projects"]["/code/a"]["mcpServers"] == {"keep": {}}
    assert reg.backup is not None and json.loads(reg.backup.read_text())["numStartups"] == 7


def test_register_cursor_and_desktop_create_their_files(home: Path) -> None:
    clients: list[onboarding.Client] = ["cursor", "claude-desktop"]
    for client in clients:
        reg = register(client, COMMAND, home)
        assert reg.path == config_path(client, home) and reg.backup is None
        entry = json.loads(reg.path.read_text())["mcpServers"]["grepwise"]
        assert entry == {"command": "/opt/bin/grepwise", "args": ["mcp"]}


def test_setup_print_changes_nothing(home: Path) -> None:
    out = CliRunner().invoke(cli.app, ["setup", "--print", "--client", "cursor"])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["mcpServers"]["grepwise"]["args"] == ["mcp"]
    assert not (home / ".cursor").exists() and not (home / ".grepwise").exists()


def test_setup_writes_the_config_without_the_claude_command(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)  # no `claude` command here
    out = CliRunner().invoke(cli.app, ["setup", "--skip-downloads"])
    assert out.exit_code == 0, out.output
    assert "Added the grepwise server" in out.output
    entry = json.loads((home / ".claude.json").read_text())["mcpServers"]["grepwise"]
    assert entry["args"] == ["mcp"] and (home / ".grepwise" / "grepwise.db").exists()
    again = CliRunner().invoke(cli.app, ["setup", "--skip-downloads"])
    assert "Replaced the grepwise server" in again.output  # safe to run twice


def test_doctor_names_whats_missing_and_how_to_fix_it(home: Path) -> None:
    checks = {what: (status, detail) for status, what, detail in run_checks(home, lambda: False)}
    assert checks["Embedding model"][0] == "✗" and "grepwise setup" in checks["Embedding model"][1]
    assert checks["Claude Code"][0] == "✗" and "grepwise setup" in checks["Claude Code"][1]
    assert checks["PyPI"][0] == "!"  # a warning, not a failure
    assert checks["Database"][0] == "✓"

    old = {"command": "uv", "args": ["run", "--directory", "/x/backend", "grepwise", "mcp"]}
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"grepwise": old}}))
    stale = [d for s, w, d in run_checks(home, lambda: True) if w == "Claude Code" and s == "✗"]
    assert any("--directory" in d for d in stale)
    assert CliRunner().invoke(cli.app, ["doctor"]).exit_code == 1


# --- gc and last use ---------------------------------------------------------------------


def make_source(conn: Conn, name: str, *, package: bool, used_days_ago: int) -> None:
    index_openapi(conn, FakeEmbedder(768), PETSTORE, name, "1.0")
    conn.execute(
        "UPDATE sources SET package = ?, last_used_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?) "
        "WHERE name = ?",
        (name if package else None, f"-{used_days_ago} days", name),
    )


def names(db: Path) -> set[str]:
    with closing(connect(db)) as c:
        return {r[0] for r in c.execute("SELECT name FROM sources")}


def test_gc_removes_only_stale_package_sources(home: Path) -> None:
    db = home / ".grepwise" / "grepwise.db"
    with closing(connect(db)) as conn:
        make_source(conn, "old-pkg", package=True, used_days_ago=45)
        make_source(conn, "fresh-pkg", package=True, used_days_ago=2)
        make_source(conn, "old-url", package=False, used_days_ago=90)
        conn.execute("INSERT INTO embeddings VALUES ('fake:768', 'orphan', x'0000')")
    dry = CliRunner().invoke(cli.app, ["gc", "--dry-run"])
    assert dry.exit_code == 0 and "old-pkg@1.0" in dry.output and "old-url" not in dry.output
    assert names(db) == {"old-pkg", "fresh-pkg", "old-url"}  # nothing removed

    done = CliRunner().invoke(cli.app, ["gc", "--yes"])
    assert done.exit_code == 0, done.output
    assert names(db) == {"fresh-pkg", "old-url"}
    with closing(connect(db)) as c:
        assert c.execute(
            "SELECT count(*) FROM embeddings WHERE content_hash = 'orphan'"
        ).fetchone() == (0,)
        assert c.execute("SELECT count(*) FROM embeddings").fetchone()[0] > 0  # still used: kept
    CliRunner().invoke(cli.app, ["gc", "--all", "--yes"])
    assert names(db) == {"fresh-pkg"}


def test_search_and_read_record_last_use(indexed: Conn, embedder: FakeEmbedder) -> None:
    ctx = ToolContext(indexed, get_settings(), embedder)
    assert indexed.execute(
        "SELECT count(*) FROM sources WHERE last_used_at IS NOT NULL"
    ).fetchone() == (0,)
    call_tool(ctx, "search", {"query": "pet", "libraries": ["petstore"]})
    used = {
        r[0] for r in indexed.execute("SELECT name FROM sources WHERE last_used_at IS NOT NULL")
    }
    assert used == {"petstore"}
    call_tool(ctx, "read", {"citation": "example/mini_repo@v1:web/session.ts:L6-L9"})
    used = {
        r[0] for r in indexed.execute("SELECT name FROM sources WHERE last_used_at IS NOT NULL")
    }
    assert used == {"petstore", "example/mini_repo"}
