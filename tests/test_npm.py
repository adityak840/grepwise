"""N6b: JavaScript/TypeScript projects and the npm registry (offline: fake registry, fake
tags, local folders standing in for clones)."""

import json
from pathlib import Path
from typing import Any

import pytest

from grepwise.config import get_settings
from grepwise.db.connection import Conn
from grepwise.ecosystems import HttpGet, load_project, parse_library
from grepwise.ecosystems.npm import NPM, find_package_dir, repository
from grepwise.ingest.embed import FakeEmbedder
from grepwise.ingest.pipeline import index_repo
from grepwise.resolve import ResolveError, Resolver
from grepwise.search.citations import parse_citation, split_ref
from grepwise.tools import ToolContext, call_tool, workspace
from tests.helpers import fake_fetcher

MANIFEST = {
    "name": "ui",
    "dependencies": {"recharts": "^2.15.0", "@tanstack/react-query": "^5.0.0"},
    "devDependencies": {"typescript": "~5.6.0", "local-ui": "file:../local-ui"},
}


def write(root: Path, files: dict[str, Any]) -> Path:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content))
    return root


def deps_of(root: Path) -> dict[str, tuple[str | None, bool, bool, str | None]]:
    project = load_project(root)
    assert project is not None
    return {d.name: (d.version, d.direct, d.from_registry, d.version_from)
            for d in project.dependencies}  # fmt: skip


def test_package_lock_v3(tmp_path: Path) -> None:
    lock = {"lockfileVersion": 3, "packages": {
        "": {"name": "ui"},
        "node_modules/recharts": {"version": "2.15.0", "resolved": "https://registry.npmjs.org/r"},
        "node_modules/@tanstack/react-query": {"version": "5.59.0"},
        "node_modules/typescript": {"version": "5.6.3"},
        "node_modules/local-ui": {"resolved": "../local-ui", "link": True},
        "node_modules/clsx": {"version": "2.1.1"},  # pulled in by recharts
        "node_modules/recharts/node_modules/clsx": {"version": "1.0.0"},  # a private copy
    }}  # fmt: skip
    deps = deps_of(write(tmp_path, {"package.json": MANIFEST, "package-lock.json": lock}))
    assert deps["recharts"] == ("2.15.0", True, True, "package-lock.json")
    assert deps["@tanstack/react-query"][0] == "5.59.0"
    assert deps["clsx"] == ("2.1.1", False, True, "package-lock.json")  # transitive, top level
    assert deps["local-ui"][2] is False  # a file: dependency


def test_pnpm_lock_with_peer_suffixes(tmp_path: Path) -> None:
    lock = """
lockfileVersion: '9.0'
importers:
  .:
    dependencies:
      recharts:
        specifier: ^2.15.0
        version: 2.15.0(react-dom@18.3.1(react@18.3.1))(react@18.3.1)
      '@tanstack/react-query':
        specifier: ^5.0.0
        version: 5.59.0(react@18.3.1)
    devDependencies:
      typescript:
        specifier: ~5.6.0
        version: 5.6.3
      local-ui:
        specifier: file:../local-ui
        version: link:../local-ui
"""
    deps = deps_of(write(tmp_path, {"package.json": MANIFEST, "pnpm-lock.yaml": lock}))
    assert deps["recharts"][:2] == ("2.15.0", True)
    assert deps["@tanstack/react-query"][0] == "5.59.0"
    assert deps["local-ui"][2] is False


def test_yarn_classic_and_berry(tmp_path: Path) -> None:
    classic = """# yarn lockfile v1

"@tanstack/react-query@^5.0.0":
  version "5.59.0"
  resolved "https://registry.yarnpkg.com/@tanstack/react-query/-/x.tgz"

recharts@^2.12.0, recharts@^2.15.0:
  version "2.15.0"

typescript@~5.6.0:
  version "5.6.3"
"""
    deps = deps_of(write(tmp_path / "a", {"package.json": MANIFEST, "yarn.lock": classic}))
    assert deps["recharts"][0] == "2.15.0" and deps["@tanstack/react-query"][0] == "5.59.0"
    berry = """__metadata:
  version: 8

"recharts@npm:^2.15.0":
  version: 2.15.0
  resolution: "recharts@npm:2.15.0"

"@tanstack/react-query@npm:^5.0.0":
  version: 5.59.0
"""
    deps = deps_of(write(tmp_path / "b", {"package.json": MANIFEST, "yarn.lock": berry}))
    assert deps["recharts"][0] == "2.15.0" and deps["@tanstack/react-query"][0] == "5.59.0"


def test_installed_node_modules_fill_the_gaps(tmp_path: Path) -> None:
    root = write(tmp_path, {
        "package.json": {"name": "x", "dependencies": {"zod": "^3.0.0"}},
        "node_modules/zod/package.json": {"name": "zod", "version": "3.23.8"},
    })  # fmt: skip
    assert deps_of(root)["zod"] == ("3.23.8", True, True, "node_modules")


def test_a_workspace_package_uses_the_lockfile_above_it(tmp_path: Path) -> None:
    """npm workspaces: one package-lock.json at the root; a member's own copy wins over the
    hoisted one."""
    repo = write(tmp_path, {
        "package.json": {"name": "root", "workspaces": ["packages/*"]},
        "package-lock.json": {"lockfileVersion": 3, "packages": {
            "node_modules/recharts": {"version": "2.15.0"},
            "packages/web/node_modules/recharts": {"version": "2.12.7"},
            "node_modules/zod": {"version": "3.23.8"},
        }},
        "packages/web/package.json": {"name": "web",
                                      "dependencies": {"recharts": "^2.12", "zod": "^3"}},
    })  # fmt: skip
    deps = deps_of(repo / "packages" / "web")
    assert deps["recharts"][0] == "2.12.7" and deps["zod"][0] == "3.23.8"
    assert set(deps) == {"recharts", "zod"}  # no transitive list away from the lockfile


@pytest.mark.parametrize(
    ("info", "repo", "folder"),
    [
        ({"repository": "github:recharts/recharts"}, "https://github.com/recharts/recharts", None),
        ({"repository": "colinhacks/zod"}, "https://github.com/colinhacks/zod", None),
        ({"repository": {"type": "git", "url": "git+https://github.com/microsoft/playwright.git",
                         "directory": "packages/playwright-core"}},
         "https://github.com/microsoft/playwright", "packages/playwright-core"),
        ({"repository": {"url": "git+ssh://git@github.com/TanStack/query.git",
                         "directory": "./packages/react-query/"}},
         "https://github.com/TanStack/query", "packages/react-query"),
        ({"repository": {"url": "https://gitlab.com/o/r.git"},
          "homepage": "https://github.com/o/r#readme"}, "https://github.com/o/r", None),
        ({"repository": "gitlab:o/r"}, None, None),
        ({"repository": {"url": "https://github.com/o/r", "directory": "../escape"}},
         "https://github.com/o/r", None),
    ],
)  # fmt: skip
def test_repository_field_shapes(
    info: dict[str, Any], repo: str | None, folder: str | None
) -> None:
    found = repository(info)
    assert (found.repo, found.subpath) == (repo, folder)


def test_monorepo_tags_name_the_package_first() -> None:
    tags = NPM.tag_candidates("@tanstack/react-query", "5.59.0")
    assert tags[:2] == ["@tanstack/react-query@5.59.0", "react-query@5.59.0"]
    assert "v5.59.0" in tags


class FakeNpm:
    def __init__(self, versions: dict[str, dict[str, Any]]):
        self.versions = versions

    def __call__(self, url: str) -> dict[str, Any] | None:
        for key, info in self.versions.items():
            if url.endswith(key):
                return info
        return None


def test_resolve_from_the_npm_registry(conn: Conn) -> None:
    npm = FakeNpm({
        "/@tanstack%2Freact-query/5.59.0": {"repository": {
            "url": "git+https://github.com/TanStack/query.git",
            "directory": "packages/react-query"}},
        "/@tanstack%2Freact-query/latest": {"version": "5.59.0"},
    })  # fmt: skip
    tags = ["v5.59.0", "@tanstack/react-query@5.59.0"]
    r = Resolver(conn, ecosystem="npm", known={}, http_get=npm, tags=lambda _: tags)
    res = r.resolve("@tanstack/react-query", "5.59.0")
    assert (res.repo_url, res.tag, res.subpath, res.ecosystem) == (
        "https://github.com/TanStack/query", "@tanstack/react-query@5.59.0",
        "packages/react-query", "npm",
    )  # fmt: skip
    again = r.resolve("@tanstack/react-query", "5.59.0")
    assert again.cached and again.subpath == "packages/react-query"
    assert r.latest_version("@tanstack/react-query") == "5.59.0"
    with pytest.raises(ResolveError, match="DefinitelyTyped"):
        r.resolve("@types/node", "22.0.0")
    with pytest.raises(ResolveError, match="not on npm"):
        r.resolve("left-pad-nope", "1.0.0")


def monorepo(root: Path) -> Path:
    return write(root, {
        "package.json": {"name": "playwright-internal", "private": True},
        "README.md": "# Playwright\n\nThe repo readme.\n",
        "docs/src/browsers.md": "# Browsers\n\nHeadless shell is used when headless.\n",
        "packages/playwright-core/package.json": {"name": "playwright-core"},
        "packages/playwright-core/src/server/registry/index.ts":
            "export function executablePath(): string {\n  return 'chromium';\n}\n",
        "packages/other/package.json": {"name": "other"},
        "packages/other/src/x.ts": "export const x = 1;\n",
        "tests/big.spec.ts": "test('x', () => {});\n",
    })  # fmt: skip


def test_find_package_dir(tmp_path: Path) -> None:
    root = monorepo(tmp_path)
    assert find_package_dir(root, "playwright-core") == "packages/playwright-core"
    # ag-grid: a stale scripts/package.json with the same name, shallower than the real one.
    write(root, {"scripts/package.json": {"name": "playwright-core", "version": "1.0.0"},
                 "packages/playwright-core/package.json":
                     {"name": "playwright-core", "version": "1.61.0"}})  # fmt: skip
    assert find_package_dir(root, "playwright-core", "1.61.0") == "packages/playwright-core"
    assert find_package_dir(root, "playwright-core") == "scripts"  # no version: shallowest
    assert find_package_dir(root, "playwright-internal") is None  # the root itself
    assert find_package_dir(root, "missing") is None


def test_a_monorepo_package_indexes_its_folder_and_the_repo_docs(
    conn: Conn, embedder: FakeEmbedder, tmp_path: Path
) -> None:
    root = monorepo(tmp_path)
    s = index_repo(
        conn, embedder, "https://github.com/microsoft/playwright", "v1.61.0",
        name="playwright-core", version="1.61.0", package="playwright-core", ecosystem="npm",
        locate=lambda r: find_package_dir(r, "playwright-core"), fetcher=fake_fetcher(root),
    )  # fmt: skip
    assert s.subpath == "packages/playwright-core"
    paths = {p for (p,) in conn.execute("SELECT path FROM documents")}
    assert paths == {
        "README.md", "docs/src/browsers.md", "packages/playwright-core/package.json",
        "packages/playwright-core/src/server/registry/index.ts",
    }  # fmt: skip
    row = conn.execute("SELECT name, ecosystem, subpath FROM sources").fetchone()
    assert row == ("playwright-core", "npm", "packages/playwright-core")


def test_scoped_names_in_refs_and_citations() -> None:
    assert split_ref("@tanstack/react-query@5.59.0") == ("@tanstack/react-query", "5.59.0")
    assert split_ref("@tanstack/react-query") == ("@tanstack/react-query", "")
    assert parse_library("npm:@scope/pkg@1.0") == ("npm", "@scope/pkg", "1.0")
    assert parse_library("typer") == (None, "typer", "")
    assert parse_library("https://x") == (None, "https://x", "")  # not an ecosystem prefix
    c = parse_citation("@tanstack/react-query@5.59.0:packages/react-query/src/a.ts:L1-L9")
    assert (c.name, c.version, c.path) == ("@tanstack/react-query", "5.59.0",
                                           "packages/react-query/src/a.ts")  # fmt: skip


# --- A Python backend next to a JS ui/ folder, through the MCP tools --------------------


@pytest.fixture
def mixed(tmp_path: Path) -> Path:
    repo = write(tmp_path / "repo", {
        ".git/HEAD": "ref: refs/heads/main\n",
        "backend/pyproject.toml": '[project]\nname = "api"\ndependencies = ["recharts"]\n',
        "backend/requirements.txt": "recharts==0.1.0\n",  # a name in both ecosystems
        "ui/package.json": {"name": "ui", "dependencies": {"recharts": "^2.15.0"}},
        "ui/package-lock.json": {"lockfileVersion": 3, "packages": {
            "node_modules/recharts": {"version": "2.15.0"}}},
    })  # fmt: skip
    return repo


@pytest.fixture
def ctx(conn: Conn, embedder: FakeEmbedder, mixed: Path, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch) -> ToolContext:  # fmt: skip
    clone = write(tmp_path / "clone", {
        "package.json": {"name": "recharts"},
        "src/chart/LineChart.tsx": "export function LineChart(props: Props) {\n"
                                   "  return generateCategoricalChart(props);\n}\n",
    })  # fmt: skip
    registries: dict[str, HttpGet] = {
        "npm": FakeNpm({"/recharts/2.15.0": {"repository": "github:recharts/recharts"}}),
        "pypi": lambda url: None,  # PyPI's recharts 0.1.0 doesn't exist here
    }

    def resolver(c: Conn, ecosystem: str = "pypi", **kw: Any) -> Resolver:
        return Resolver(c, ecosystem=ecosystem, known={}, http_get=registries[ecosystem],
                        tags=lambda _: ["v2.15.0"], **kw)  # fmt: skip

    monkeypatch.setattr(workspace, "Resolver", resolver)
    real = index_repo
    monkeypatch.setattr(workspace, "index_repo",
                        lambda *a, **k: real(*a, fetcher=fake_fetcher(clone), **k))  # fmt: skip
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return ToolContext(conn, get_settings(), embedder, cwd=mixed)


def test_libraries_lists_both_projects(ctx: ToolContext) -> None:
    out = call_tool(ctx, "libraries", {})
    assert [(p["root"].rsplit("/", 1)[-1], p["ecosystem"]) for p in out["projects"]] == [
        ("backend", "pypi"), ("ui", "npm"),
    ]  # fmt: skip
    assert {(d["name"], d["ecosystem"], d["version"]) for d in out["dependencies"]} == {
        ("recharts", "pypi", "0.1.0"), ("recharts", "npm", "2.15.0"),
    }  # fmt: skip


def test_a_js_dependency_is_indexed_on_first_search(ctx: ToolContext) -> None:
    out = call_tool(ctx, "search", {"query": "LineChart", "libraries": ["npm:recharts"]})
    assert out["results"][0]["source"] == "recharts@2.15.0"
    assert out["results"][0]["link"].startswith(
        "https://github.com/recharts/recharts/blob/v2.15.0/src/chart/LineChart.tsx"
    )
    row = ctx.conn.execute("SELECT ecosystem, package FROM sources").fetchone()
    assert row == ("npm", "recharts")
    # Plain 'recharts' means both dependencies: npm's is indexed, PyPI's can't be resolved.
    both = call_tool(ctx, "search", {"query": "LineChart", "libraries": ["recharts"]})
    assert both["results"][0]["source"] == "recharts@2.15.0"
    assert any("Couldn't index recharts 0.1.0" in n for n in both["notes"])


def test_hints_name_the_projects_registries(ctx: ToolContext) -> None:
    out = call_tool(ctx, "search", {"query": "x", "libraries": ["zod"]})
    assert "PyPI or npm has no zod" in out["notes"][0]


def test_types_packages_dont_match_a_plain_name(ctx: ToolContext) -> None:
    ctx.conn.execute(
        "INSERT INTO sources (type, name, url, version, package, ecosystem, status) "
        "VALUES ('repo', '@types/node', 'u', '22.0.0', '@types/node', 'npm', 'ready')"
    )
    out = call_tool(ctx, "search", {"query": "x", "libraries": ["node"]})
    assert out["results"] == [] and "isn't in" in out["notes"][0]


def test_add_targets() -> None:
    from grepwise.commands import _package_target

    assert _package_target("typer==0.27.2") == ("pypi", "typer", "0.27.2")
    assert _package_target("Typer") == ("pypi", "typer", None)
    assert _package_target("npm:recharts") == ("npm", "recharts", None)
    assert _package_target("npm:@TanStack/react-query@5.59.0") == (
        "npm", "@tanstack/react-query", "5.59.0",
    )  # fmt: skip
