"""N3: plain library names, on-demand indexing, background embedding, links, read, libraries.
A fake PyPI and a local folder stand in for the network (offline)."""

from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from grepwise.config import get_settings
from grepwise.db.connection import Conn, connect
from grepwise.ingest import pipeline
from grepwise.ingest.embed import FakeEmbedder
from grepwise.ingest.pipeline import embed_missing, embed_priority, index_repo
from grepwise.resolve import ResolveError, Resolver
from grepwise.search.links import link_for
from grepwise.tools import ToolContext, call_tool, workspace
from grepwise.worker import EmbeddingWorker, release_lease, take_lease
from tests.helpers import DOCS_URL, FIXTURES, fake_fetcher

LOCK = """
version = 1
[[package]]
name = "app"
version = "0.1.0"
source = { editable = "." }
[[package]]
name = "docslib"
version = "2.0"
source = { registry = "https://pypi.org/simple" }
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname = "app"\ndependencies = ["docslib"]\n')
    (root / "uv.lock").write_text(LOCK)
    return root


@pytest.fixture
def ctx(
    conn: Conn, db_path: Path, embedder: FakeEmbedder, project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ToolContext:  # fmt: skip
    """docslib 2.0 'lives' at example/docs_repo, tag v2.0 (really the docs fixture)."""
    pypi = lambda url: {"info": {"project_urls": {"Source": DOCS_URL}}}  # noqa: E731
    monkeypatch.setattr(
        workspace, "Resolver",
        lambda c, **kw: Resolver(c, known={}, http_get=pypi, tags=lambda _: ["v2.0"], **kw),
    )  # fmt: skip
    real = pipeline.index_repo

    def index_repo_offline(*args: Any, **kwargs: Any) -> pipeline.IndexSummary:
        return real(*args, fetcher=fake_fetcher(FIXTURES / "docs_repo"), **kwargs)

    monkeypatch.setattr(workspace, "index_repo", index_repo_offline)
    worker = EmbeddingWorker(db_path, embedder)
    return ToolContext(conn, get_settings(), embedder, worker=worker, cwd=project)


def status_of(ctx: ToolContext, name: str) -> str:
    deps = call_tool(ctx, "libraries", {})["dependencies"]
    return str(next(d["status"] for d in deps if d["name"] == name))


def test_first_search_indexes_the_dependency_and_answers_from_keywords(ctx: ToolContext) -> None:
    assert status_of(ctx, "docslib") == "not indexed"
    out = call_tool(ctx, "search", {"query": "required option", "libraries": ["docslib"]})
    assert out["results"], out
    assert all(r["found_by"]["vector"] is None for r in out["results"])  # no vectors yet
    assert out["results"][0]["source"] == "docslib@2.0"
    assert out["results"][0]["link"].startswith(
        "https://github.com/example/docs_repo/blob/v2.0/"
    )  # the tag, not the package version
    assert "was just indexed" in out["notes"][0]
    assert "Semantic search is off until docslib@2.0 (0%)" in out["notes"][1]
    assert status_of(ctx, "docslib") == "embedding 0%"

    assert ctx.worker is not None
    ctx.worker.run_until_idle()  # the background worker's job, run here
    assert status_of(ctx, "docslib") == "ready"
    again = call_tool(ctx, "search", {"query": "required option", "libraries": ["docslib"]})
    assert any(r["found_by"]["vector"] for r in again["results"])
    assert again["notes"] == []  # nothing indexed this time


def test_leaving_libraries_out_searches_the_projects_dependencies(
    ctx: ToolContext, indexed: Conn
) -> None:
    project = workspace.load_project(ctx, None)
    assert workspace.default_sources(ctx.conn, project) == []  # docslib isn't indexed yet
    empty = call_tool(ctx, "search", {"query": "refreshToken"})
    assert empty["results"] == [] and "None of this project's dependencies" in empty["notes"][0]
    call_tool(ctx, "search", {"query": "x", "libraries": ["docslib"]})  # index docslib
    refs = {s.ref for s in workspace.default_sources(ctx.conn, project)}
    assert refs == {"docslib@2.0"}  # hand-added sources are reached by naming them
    named = call_tool(ctx, "search", {"query": "refreshToken", "libraries": ["mini_repo"]})
    assert named["results"][0]["source"] == "example/mini_repo@v1"
    no_project = workspace.default_sources(ctx.conn, None)
    assert {s.ref for s in no_project} >= {"docslib@2.0", "example/mini_repo@v1"}  # everything


def test_a_repo_indexed_by_url_counts_as_the_dependency(ctx: ToolContext) -> None:
    index_repo(
        ctx.conn, ctx.embedder, DOCS_URL, "v2.0", fetcher=fake_fetcher(FIXTURES / "docs_repo")
    )
    ctx.conn.execute(
        "INSERT INTO resolutions (ecosystem, package, version, repo_url, tag) "
        "VALUES ('pypi', 'docslib', '2.0', ?, 'v2.0')", (DOCS_URL,),
    )  # fmt: skip
    out = call_tool(ctx, "search", {"query": "required option", "libraries": ["docslib"]})
    assert {r["source"] for r in out["results"]} == {"example/docs_repo@v2.0"}
    assert out["notes"] == []  # found, so nothing was indexed again
    assert status_of(ctx, "docslib") == "ready"


def test_unknown_and_busy_libraries_become_notes(ctx: ToolContext) -> None:
    unknown = call_tool(ctx, "search", {"query": "x", "libraries": ["docslb"]})
    assert unknown["results"] == [] and "Did you mean docslib" in unknown["notes"][0]
    ctx.conn.execute(
        "INSERT INTO sources (type, name, url, version, package, ecosystem, status) "
        "VALUES ('repo', 'docslib', ?, '2.0', 'docslib', 'pypi', 'indexing')", (DOCS_URL,),
    )  # fmt: skip
    busy = call_tool(ctx, "search", {"query": "x", "libraries": ["docslib"]})
    assert "being indexed by another session" in busy["notes"][0]


def test_project_argument_beats_the_working_folder(ctx: ToolContext, tmp_path: Path) -> None:
    project = str(ctx.cwd)
    ctx.cwd = tmp_path / "elsewhere"
    ctx.cwd.mkdir()
    assert call_tool(ctx, "libraries", {})["project"] is None
    out = call_tool(ctx, "libraries", {"project": project})
    assert out["project"] == project and out["dependencies"][0]["name"] == "docslib"


# --- Background embedding ----------------------------------------------------------------


def test_embedding_order_and_lease(ctx: ToolContext, db_path: Path) -> None:
    assert [embed_priority(p, k) for p, k in [
        ("docs/a.md", "section"), ("src/pkg/core.py", "function"),
        ("examples/demo.py", "module"), ("tests/test_core.py", "function"),
        ("src/test_utils.py", "function"),
    ]] == [0, 1, 2, 3, 3]  # fmt: skip

    call_tool(ctx, "search", {"query": "x", "libraries": ["docslib"]})
    (source_id,) = ctx.conn.execute("SELECT id FROM sources WHERE package = 'docslib'").fetchone()
    calls = iter([True, False])  # stop after the first batch
    n = embed_missing(ctx.conn, ctx.embedder, source_id, batch=1, keep_going=lambda: next(calls))
    assert n == 1
    first = ctx.conn.execute(
        "SELECT c.kind FROM chunks c JOIN embeddings e ON e.content_hash = c.content_hash "
        "WHERE c.source_id = ?", (source_id,),
    ).fetchall()  # fmt: skip
    assert first == [("section",)]  # docs go first

    with closing(connect(db_path)) as other:
        assert take_lease(ctx.conn, source_id)
        assert not take_lease(other, source_id)  # another process can't take it
        release_lease(ctx.conn, source_id)
        assert take_lease(other, source_id)


def test_worker_resumes_unfinished_sources(ctx: ToolContext) -> None:
    call_tool(ctx, "search", {"query": "x", "libraries": ["docslib"]})
    assert ctx.worker is not None
    fresh = EmbeddingWorker(ctx.worker.db_path, ctx.embedder)  # as after a restart
    fresh.enqueue_unfinished()
    fresh.run_until_idle()
    assert status_of(ctx, "docslib") == "ready"


# --- read --------------------------------------------------------------------------------


def test_read_by_citation_by_path_and_by_endpoint(ctx: ToolContext, indexed: Conn) -> None:
    out = call_tool(ctx, "read", {"citation": "example/mini_repo@v1:web/session.ts:L6-L9"})
    assert out["content"].splitlines()[0].startswith("    6  export async function refreshToken(")
    assert out["link"] == "https://github.com/example/mini_repo/blob/v1/web/session.ts#L6-L9"
    wider = call_tool(ctx, "read", {"citation": "example/mini_repo@v1:web/session.ts:L6-L9",
                                    "start_line": 1})  # fmt: skip
    assert wider["start_line"] == 1 and wider["end_line"] == 9
    by_path = call_tool(ctx, "read", {"library": "example/mini_repo", "path": "README.md"})
    assert by_path["citation"].startswith("example/mini_repo@v1:README.md:L1-")
    endpoint = call_tool(ctx, "read", {"library": "petstore", "path": "GET /pet/42"})
    assert endpoint["citation"].startswith("petstore@1.0.27:GET /pet/{petId}:L1-")
    assert endpoint["link"] is None


def test_read_errors(ctx: ToolContext, indexed: Conn) -> None:
    def code(args: dict[str, Any]) -> str:
        return str(call_tool(ctx, "read", args)["error"]["code"])

    assert code({"citation": "session.ts lines 6 to 9"}) == "bad_input"
    assert code({"citation": "example/mini_repo@v1:web/session.ts:L6-L999"}) == "bad_line_range"
    assert code({"citation": "nope@1:a.py:L1-L2"}) == "source_not_found"
    assert code({"library": "nothing", "path": "a.py"}) == "source_not_found"
    assert code({"library": "example/mini_repo"}) == "bad_input"  # path missing


# --- Links -------------------------------------------------------------------------------


def test_links() -> None:
    gh = "https://github.com/pallets/click"
    assert link_for(gh, "8.5.0", "8.5.0", "src/click/core.py", 10, 20, "repo") == (
        "https://github.com/pallets/click/blob/8.5.0/src/click/core.py#L10-L20"
    )
    assert link_for(gh, None, "8.5.0", "a b.md", 3, 3, "docs") == (
        "https://github.com/pallets/click/blob/8.5.0/a%20b.md#L3"
    )  # before N2 there was no ref: the version is the tag; spaces encoded; one line
    assert link_for("file:///Users/me/lib", "abc", "abc", "src/x.py", 7, 9, "repo") == (
        "/Users/me/lib/src/x.py:7"
    )
    assert link_for("https://petstore/openapi.json", None, "1", "GET /pet", 1, 9, "openapi") is None


def test_a_repo_indexed_by_url_is_found_on_first_use(ctx: ToolContext) -> None:
    """No cached resolution yet: resolving finds the URL source, so nothing is re-indexed."""
    index_repo(
        ctx.conn, ctx.embedder, DOCS_URL, "v2.0", fetcher=fake_fetcher(FIXTURES / "docs_repo")
    )
    out = call_tool(ctx, "search", {"query": "required option", "libraries": ["docslib"]})
    assert {r["source"] for r in out["results"]} == {"example/docs_repo@v2.0"}
    assert ctx.conn.execute("SELECT count(*) FROM sources").fetchone() == (1,)


def test_a_partly_embedded_source_stays_out_of_vector_search(ctx: ToolContext) -> None:
    call_tool(ctx, "search", {"query": "x", "libraries": ["docslib"]})
    (source_id,) = ctx.conn.execute("SELECT id FROM sources WHERE package = 'docslib'").fetchone()
    calls = iter([True, False])
    embed_missing(ctx.conn, ctx.embedder, source_id, batch=1, keep_going=lambda: next(calls))
    out = call_tool(ctx, "search", {"query": "required option", "libraries": ["docslib"]})
    assert all(r["found_by"]["vector"] is None for r in out["results"])  # 1 chunk isn't enough


def test_one_unfinished_source_turns_vectors_off_for_the_whole_search(
    ctx: ToolContext, indexed: Conn
) -> None:
    """Otherwise the vector list holds only the finished source's chunks, and fusion ranks
    them as highly as the keyword matches (a requests changelog first for a Playwright
    question)."""
    both = {"query": "required option", "libraries": ["docslib", "mini_repo"]}
    call_tool(ctx, "search", both)  # indexes docslib, without vectors
    out = call_tool(ctx, "search", both)
    assert out["results"] and all(r["found_by"]["vector"] is None for r in out["results"])
    assert any("off until docslib@2.0 (0%) is embedded" in n for n in out["notes"])
    assert not any("mini_repo" in n for n in out["notes"])  # it's fully embedded
    assert ctx.worker is not None
    ctx.worker.run_until_idle()
    done = call_tool(ctx, "search", both)
    assert any(r["found_by"]["vector"] for r in done["results"]) and done["notes"] == []


def test_add_hints_only_name_what_would_work(
    ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`grepwise add <name>` looks the name up on PyPI: suggest it only when PyPI has it."""
    on_pypi = {"pypi.org/pypi/requests/json", "pypi.org/pypi/requests/2.0/json"}

    def pypi(url: str) -> dict[str, Any] | None:
        return {"info": {}} if url.split("://")[1] in on_pypi else None

    monkeypatch.setattr(workspace, "Resolver", lambda c, **kw: Resolver(c, http_get=pypi, **kw))

    def note(lib: str) -> str:
        out = call_tool(ctx, "search", {"query": "x", "libraries": [lib]})
        return str(out["notes"][0])

    assert "`grepwise add requests`" in note("requests")
    assert "`grepwise add requests==2.0`" in note("requests@2.0")
    for npm in ("recharts", "playwright-core@1.61.0"):
        assert "PyPI has no" in note(npm) and "grepwise add https://github.com/" in note(npm)
        assert "`grepwise add recharts`" not in note(npm)
    assert "It isn't a package name" in note("vercel/next.js")

    def offline(url: str) -> None:
        raise ResolveError("Couldn't reach PyPI")

    monkeypatch.setattr(workspace, "Resolver", lambda c, **kw: Resolver(c, http_get=offline, **kw))
    assert "if it's on PyPI, or else" in note("recharts")


def test_another_version_of_an_indexed_repo_suggests_its_url(
    ctx: ToolContext, indexed: Conn
) -> None:
    out = call_tool(ctx, "search", {"query": "x", "libraries": ["mini_repo@v9"]})
    assert "isn't indexed" in out["notes"][0]
    assert "grepwise add https://github.com/example/mini_repo --ref <tag for v9>" in out["notes"][0]


def test_naming_the_locked_version_indexes_it_too(ctx: ToolContext) -> None:
    """'docslib@2.0' is the project's own version: indexed on demand like 'docslib'."""
    out = call_tool(ctx, "search", {"query": "required option", "libraries": ["docslib@2.0"]})
    assert out["results"] and out["results"][0]["source"] == "docslib@2.0"
    assert "was just indexed" in out["notes"][0]
    other = call_tool(ctx, "search", {"query": "x", "libraries": ["docslib@1.0"]})
    assert other["results"] == [] and "isn't indexed" in other["notes"][0]


def test_a_plain_name_finds_an_owner_repo_source(ctx: ToolContext, indexed: Conn) -> None:
    """People say 'mini_repo', not 'example/mini_repo'; and a tag works as the version."""
    for names in (["mini_repo"], ["MINI_REPO@v1"], ["example/mini_repo"]):
        out = call_tool(ctx, "search", {"query": "refreshToken", "libraries": names})
        assert out["results"][0]["source"] == "example/mini_repo@v1", names
        assert out["notes"] == []
    missing = call_tool(ctx, "search", {"query": "x", "libraries": ["mini_repo@v9"]})
    assert missing["results"] == [] and "isn't indexed" in missing["notes"][0]
