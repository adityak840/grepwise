"""Reading a project's dependencies: pyproject.toml, uv.lock, poetry.lock, requirements, .venv."""

from pathlib import Path

import pytest

from grepwise.ecosystems import Project, find_roots, load_project
from grepwise.ecosystems.pypi import normalize_name

PYPROJECT = """
[project]
name = "My_App"
dependencies = ["Typer>=0.9", "rich[jupyter]; python_version > '3.9'", "local-lib"]

[project.optional-dependencies]
docs = ["mkdocs"]

[dependency-groups]
dev = ["pytest>=8", {include-group = "lint"}]
lint = ["ruff"]
"""

UV_LOCK = """
version = 1

[[package]]
name = "my-app"
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

[[package]]
name = "local-lib"
version = "0.0.1"
source = { git = "https://example.com/local-lib.git" }

[[package]]
name = "pytest"
version = "9.1.1"
source = { registry = "https://pypi.org/simple" }
"""


def write(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _load(root: Path) -> Project:
    project = load_project(root)
    assert project is not None
    return project


def test_normalize_name() -> None:
    assert normalize_name("Markdown_It.py") == "markdown-it-py"
    assert normalize_name("PyYAML") == "pyyaml"


def test_uv_project(tmp_path: Path) -> None:
    project = _load(write(tmp_path, {"pyproject.toml": PYPROJECT, "uv.lock": UV_LOCK}))
    assert [(r.ecosystem, r.name) for r in project.roots] == [("pypi", "my-app")]
    deps = {d.name: d for d in project.dependencies}
    assert "my-app" not in deps  # the project itself
    assert (deps["typer"].version, deps["typer"].direct, deps["typer"].version_from) == (
        "0.27.2", True, "uv.lock",
    )  # fmt: skip
    assert deps["click"].direct is False  # only in the lockfile: pulled in by typer
    assert deps["local-lib"].from_registry is False  # a git source
    assert deps["pytest"].direct and deps["ruff"].direct and deps["mkdocs"].direct
    assert deps["ruff"].version is None  # declared, but not locked
    assert [d.direct for d in project.dependencies] == sorted(
        (d.direct for d in project.dependencies), reverse=True
    )  # direct ones first


def test_poetry_project(tmp_path: Path) -> None:
    root = write(tmp_path, {
        "pyproject.toml": '[tool.poetry]\nname = "svc"\n[tool.poetry.dependencies]\n'
                          'python = "^3.12"\nrequests = "^2.32"\n'
                          '[tool.poetry.group.dev.dependencies]\nblack = "*"\n',
        "poetry.lock": '[[package]]\nname = "requests"\nversion = "2.32.3"\n\n'
                       '[[package]]\nname = "black"\nversion = "24.10.0"\n\n'
                       '[[package]]\nname = "private"\nversion = "1.0"\n'
                       '[package.source]\ntype = "git"\nurl = "x"\n',
    })  # fmt: skip
    deps = {d.name: d for d in _load(root).dependencies}
    assert "python" not in deps
    assert (deps["requests"].version, deps["requests"].version_from) == ("2.32.3", "poetry.lock")
    assert deps["black"].direct and deps["private"].from_registry is False


def test_requirements_pins(tmp_path: Path) -> None:
    root = write(tmp_path, {
        "requirements.txt": "# app\nFlask==3.1.0\n"
                            "requests[socks]==2.32.3 ; python_version >= '3.8'\n"
                            "httpx>=0.27\n-r requirements-dev.txt\n",
        "requirements-dev.txt": "pytest==8.3.4\n",
    })  # fmt: skip
    deps = {d.name: d for d in _load(root).dependencies}
    assert deps["flask"].version == "3.1.0" and deps["requests"].version == "2.32.3"
    assert deps["httpx"].version is None and deps["httpx"].direct  # declared, not pinned
    assert deps["pytest"].version == "8.3.4"


def test_installed_venv_fills_the_gaps(tmp_path: Path) -> None:
    root = write(tmp_path, {
        "pyproject.toml": '[project]\nname = "x"\ndependencies = ["attrs"]\n',
        ".venv/lib/python3.12/site-packages/attrs-24.2.0.dist-info/METADATA":
            "Metadata-Version: 2.1\nName: attrs\nVersion: 24.2.0\n",
    })  # fmt: skip
    dep = _load(root).get("attrs")
    assert dep is not None and (dep.version, dep.version_from) == ("24.2.0", ".venv")


def test_find_roots_walks_up_but_not_to_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    project = write(tmp_path / "code" / "app", {"pyproject.toml": "", "src/app/x.py": ""})
    assert [p for p, _ in find_roots(project / "src" / "app")] == [project]
    write(tmp_path, {"pyproject.toml": ""})  # a stray file in the home folder
    assert find_roots(tmp_path / "code" / "notes") == []  # (code/notes doesn't exist)
    assert find_roots(tmp_path / "code") == []  # not a project or repo: doesn't look down


def test_a_folder_holding_projects_finds_them_one_level_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repo root with backend/ and tools/ projects; hidden and junk folders are skipped."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = write(tmp_path / "repo", {
        ".git/HEAD": "ref: refs/heads/main\n",
        "backend/pyproject.toml": '[project]\nname = "api"\ndependencies = ["typer"]\n',
        "backend/requirements.txt": "typer==0.27.2\n",
        "tools/requirements.txt": "rich==14.0.0\n",
        ".hidden/requirements.txt": "nope==1\n",
        "node_modules/requirements.txt": "nope==1\n",
        "README.md": "",
    })  # fmt: skip
    project = _load(repo)
    assert project.root == repo  # no project above: the starting folder
    assert [r.path.name for r in project.roots] == ["backend", "tools"]
    assert {d.name: d.version for d in project.dependencies} == {
        "typer": "0.27.2", "rich": "14.0.0",
    }  # fmt: skip
    assert project.get("Typer", "pypi") is not None and project.get("typer", "npm") is None
    inner = _load(repo / "backend")
    assert [r.path for r in inner.roots] == [repo / "backend"]  # tools/ isn't below it


def test_a_python_project_doesnt_add_its_own_subfolders(tmp_path: Path) -> None:
    """examples/requirements.txt in a Python project is the same ecosystem: not merged."""
    root = write(tmp_path, {
        "requirements.txt": "flask==3.1.0\n",
        "examples/requirements.txt": "django==5.0\n",
    })  # fmt: skip
    assert [d.name for d in _load(root).dependencies] == ["flask"]


def test_a_plain_folder_of_repos_isnt_one_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opened in ~/Documents (no project, not a repo): its repos aren't merged."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    docs = write(tmp_path / "Documents", {
        "api/requirements.txt": "flask==3.1.0\n",
        "scripts/requirements.txt": "rich==14.0.0\n",
    })  # fmt: skip
    assert load_project(docs) is None
