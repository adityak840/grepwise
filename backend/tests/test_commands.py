"""grepwise add / deps / sync / remove, through the real CLI (fake PyPI and GitHub; offline)."""

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from grepwise import cli, commands
from grepwise.config import get_settings
from grepwise.db.connection import connect
from grepwise.ingest import pipeline
from grepwise.resolve import Resolver
from tests.helpers import FIXTURES, fake_fetcher

UV_LOCK = """
version = 1
[[package]]
name = "app"
version = "0.1.0"
source = { editable = "." }
[[package]]
name = "typer"
version = "0.27.2"
source = { registry = "https://pypi.org/simple" }
[[package]]
name = "click"
version = "8.5.0"
source = { registry = "https://pypi.org/simple" }
"""


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A Grepwise home and a uv project (depending on typer) as the working directory."""
    home = tmp_path / "home"
    project = tmp_path / "app"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname = "app"\ndependencies = ["typer"]\n')
    (project / "uv.lock").write_text(UV_LOCK)
    monkeypatch.chdir(project)
    monkeypatch.setenv("GREPWISE_HOME", str(home))
    monkeypatch.setenv("GREPWISE_EMBEDDER", "fake")
    get_settings.cache_clear()
    monkeypatch.setattr(commands, "console", Console(width=300))
    monkeypatch.setattr(cli, "console", Console(width=300))
    # PyPI says every package lives in example/<name>, tagged v<version>.
    pypi = lambda url: {"info": {"project_urls": {"Source": "https://github.com/example/x"}}}  # noqa: E731
    monkeypatch.setattr(
        commands, "Resolver",
        lambda conn, **kw: Resolver(conn, known={}, http_get=pypi,
                                    tags=lambda _: ["v0.27.2", "v8.5.0"], **kw),
    )  # fmt: skip
    # "Cloning" any repo gives the docs fixture.
    real = pipeline.index_repo

    def index_repo(*args: Any, **kwargs: Any) -> pipeline.IndexSummary:
        return real(*args, fetcher=fake_fetcher(FIXTURES / "docs_repo"), **kwargs)

    monkeypatch.setattr(commands, "index_repo", index_repo)
    yield home
    get_settings.cache_clear()


def run(*args: str, input: str | None = None) -> Any:
    result = CliRunner().invoke(cli.app, list(args), input=input)
    return result


def sources(home: Path) -> list[tuple[str | None, ...]]:
    with closing(connect(home / "grepwise.db")) as c:
        return c.execute("SELECT name, version, ref, package FROM sources ORDER BY name").fetchall()


def test_add_package_uses_the_locked_version(home: Path) -> None:
    out = run("add", "typer")
    assert out.exit_code == 0, out.output
    assert "Using typer 0.27.2 from uv.lock" in out.output
    assert "https://github.com/example/x at tag v0.27.2" in out.output
    assert sources(home) == [("typer", "0.27.2", "v0.27.2", "typer")]
    again = run("add", "Typer==0.27.2")  # pinned, any spelling of the name
    assert again.exit_code == 0 and "(cached)" in again.output
    assert "0 created" in again.output  # re-indexed in place, embeddings reused


def test_add_url_needs_a_ref(home: Path) -> None:
    out = run("add", "https://github.com/example/x")
    assert out.exit_code == 1 and "needs --ref" in out.output


def test_add_local_folder(home: Path, tmp_path: Path) -> None:
    lib = tmp_path / "internal-lib"
    (lib / "src").mkdir(parents=True)
    (lib / "src" / "core.py").write_text("def helper():\n    return 1\n")
    (lib / "README.md").write_text("# Internal lib\n\n## Usage\n\nCall helper.\n")
    out = run("add", str(lib))
    assert out.exit_code == 0, out.output
    assert sources(home) == [("internal-lib", "local", None, None)]  # not a git repo
    found = run("symbol", "Usage")
    assert "internal-lib@local:README.md" in found.output  # docs split by heading


def test_deps_and_sync(home: Path) -> None:
    deps = run("deps", "--all")
    assert deps.exit_code == 0, deps.output
    assert "typer" in deps.output and "click" in deps.output and "transitive" in deps.output

    dry = run("sync", "--dry-run")
    assert dry.exit_code == 0 and "would add" in dry.output and "v0.27.2" in dry.output
    assert sources(home) == []  # dry run indexes nothing

    first = run("sync")
    assert first.exit_code == 0, first.output
    assert "added" in first.output and "click" not in first.output  # direct deps only
    second = run("sync", "--all")
    assert "indexed" in second.output and "added" in second.output  # typer done, click added
    assert {s[0] for s in sources(home)} == {"typer", "click"}
    assert "✓" in run("deps").output


def test_remove(home: Path) -> None:
    run("add", "typer")
    refused = run("remove", "typer@0.27.2", input="n\n")
    assert refused.exit_code == 1 and sources(home)
    gone = run("remove", "typer@0.27.2", "--yes")
    assert gone.exit_code == 0 and "Removed typer@0.27.2" in gone.output
    assert sources(home) == []
    with closing(connect(home / "grepwise.db")) as c:
        assert c.execute("SELECT count(*) FROM chunks_fts").fetchone() == (0,)
        assert c.execute("SELECT count(*) FROM embeddings").fetchone()[0] > 0  # kept
    assert run("remove", "typer").exit_code == 1  # needs name@version
    assert "No source" in run("remove", "nothing@1.0", "-y").output


def test_no_project_here(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    out = run("deps")
    assert out.exit_code == 1 and "No Python project here" in out.output
