"""Hybrid search, end to end on the test database (FakeEmbedder; no network)."""

import shutil
from contextlib import closing
from pathlib import Path

import pytest

from grepwise.db.connection import Conn, connect
from grepwise.ingest.embed import FakeEmbedder
from grepwise.ingest.pipeline import index_docs, index_openapi, index_repo
from grepwise.search import hybrid
from grepwise.search.citations import parse_citation
from grepwise.search.hybrid import fts_query, rrf_merge, search
from grepwise.search.vectors import CACHE
from tests.helpers import DOCS_URL, FIXTURES, PETSTORE, REPO_URL, count, fake_fetcher

# --- RRF merge (no database) -------------------------------------------------------------


def test_rrf_merge_constructed_case() -> None:
    merged = rrf_merge(vector_ids=[10, 20, 30], keyword_ids=[30, 40])
    assert [(r.chunk_id, r.vector_rank, r.keyword_rank) for r in merged] == [
        (30, 3, 1),  # 1/63 + 1/61: found by both searches, so first
        (10, 1, None),  # 1/61
        (20, 2, None),  # 1/62 ties with 40 (same best rank 2); lower chunk id first
        (40, None, 2),  # 1/62
    ]
    assert merged[0].score == pytest.approx(1 / 63 + 1 / 61)
    assert merged[1].score == pytest.approx(1 / 61)
    assert merged[2].score == merged[3].score == pytest.approx(1 / 62)


def test_rrf_merge_empty() -> None:
    assert rrf_merge([], []) == []


# --- Indexed fixtures --------------------------------------------------------------------


def test_docs_index(indexed: Conn) -> None:
    rows = indexed.execute(
        "SELECT s.type, s.subpath, d.path, d.content, d.line_count FROM documents d "
        "JOIN sources s ON s.id = d.source_id WHERE s.type = 'docs' ORDER BY d.path"
    ).fetchall()
    assert [r[2] for r in rows] == [
        "docs/components.mdx",
        "docs/plain.md",
        "docs/tutorial/options.md",
    ]  # README.md is outside --path; conf.py isn't Markdown
    for _, subpath, path, content, _ in rows:
        assert subpath == "docs"
        assert content == (FIXTURES / "docs_repo" / path).read_text()
    header = indexed.execute(
        "SELECT header FROM chunks WHERE symbol = 'Tutorial > CLI Options > Required Options'"
    ).fetchone()
    assert header == (
        "Docs: example/docs_repo\nFile: docs/tutorial/options.md\n"
        "Section: Tutorial > CLI Options > Required Options",
    )


def test_openapi_index(indexed: Conn) -> None:
    assert count(indexed, "SELECT count(*) FROM documents d JOIN sources s ON s.id = d.source_id "
                          "WHERE s.type = 'openapi'") == 9  # fmt: skip
    row = indexed.execute(
        "SELECT c.kind, c.start_line, c.end_line, c.content = d.content, s.commit_sha, c.header "
        "FROM chunks c JOIN documents d ON d.id = c.document_id "
        "JOIN sources s ON s.id = c.source_id "
        "WHERE d.path = 'GET /pet/{petId}'"
    ).fetchone()
    assert row is not None
    kind, start, end, whole, sha, header = row
    assert (kind, start, whole, sha) == ("endpoint", 1, True, None)
    assert end == 27
    assert header == "API: petstore\nEndpoint: GET /pet/{petId}"


def test_every_chunk_gets_search_text(indexed: Conn) -> None:
    assert count(indexed, "SELECT count(*) FROM chunks WHERE search_text IS NULL") == 0
    st = indexed.execute("SELECT search_text FROM chunks WHERE symbol = 'refreshToken'").fetchone()
    assert st is not None and st[0].endswith("refresh token")


# --- Search ------------------------------------------------------------------------------


def test_exact_identifier_is_first_and_found_by_keyword(
    indexed: Conn, embedder: FakeEmbedder
) -> None:
    results = search(indexed, embedder, "refreshToken")
    assert results[0].symbol == "refreshToken"
    assert results[0].keyword_rank == 1
    assert results[0].citation == "example/mini_repo@v1:web/session.ts:L6-L9"


def test_split_words_find_camel_case_code(indexed: Conn, embedder: FakeEmbedder) -> None:
    results = search(indexed, embedder, "refresh token")
    assert results[0].symbol == "refreshToken" and results[0].keyword_rank == 1


def test_english_stemming_matches_word_forms(indexed: Conn, embedder: FakeEmbedder) -> None:
    results = search(indexed, embedder, "requiring option", kinds=["section"])
    assert results[0].symbol == "Tutorial > CLI Options > Required Options"


def test_citations_parse_back(indexed: Conn, embedder: FakeEmbedder) -> None:
    for r in search(indexed, embedder, "pet", limit=20, token_budget=100_000):
        c = parse_citation(r.citation)
        assert (c.source, c.path, c.start_line, c.end_line) == (
            r.source, r.path, r.start_line, r.end_line,
        )  # fmt: skip


def test_source_filters(indexed: Conn, embedder: FakeEmbedder) -> None:
    q = "find pet by id"
    by_name = search(indexed, embedder, q, sources=["petstore"], limit=50, token_budget=10**6)
    assert by_name and {r.source for r in by_name} == {"petstore@1.0.27"}
    # FakeEmbedder vectors are random, so only the keyword rank is meaningful here. In this
    # 9-endpoint spec nearly every endpoint says "pet" and "id", so BM25 gives those words
    # almost no weight; findByStatus can edge ahead on "find" and "by".
    top2 = {r.path for r in by_name if r.keyword_rank is not None and r.keyword_rank <= 2}
    assert "GET /pet/{petId}" in top2

    by_version = search(indexed, embedder, q, sources=["petstore@1.0.27"], limit=50)
    assert {r.source for r in by_version} == {"petstore@1.0.27"}
    assert search(indexed, embedder, q, sources=["petstore@9.9.9"]) == []

    two = search(
        indexed, embedder, q, sources=["petstore", "example/mini_repo"], limit=50,
        token_budget=10**6,
    )  # fmt: skip
    assert {r.source for r in two} == {"petstore@1.0.27", "example/mini_repo@v1"}


def test_kind_filter(indexed: Conn, embedder: FakeEmbedder) -> None:
    results = search(indexed, embedder, "option", kinds=["section"], limit=50)
    assert results and {r.kind for r in results} == {"section"}
    mixed = search(indexed, embedder, "pet", kinds=["endpoint", "class"], limit=50)
    assert {r.kind for r in mixed} <= {"endpoint", "class"}


def test_limit_and_token_budget(indexed: Conn, embedder: FakeEmbedder) -> None:
    assert len(search(indexed, embedder, "pet", limit=3, token_budget=10**6)) == 3
    budgeted = search(indexed, embedder, "pet", limit=50, token_budget=500)
    assert sum(len(r.content) // 4 for r in budgeted) <= 500
    # Always at least one result, even when the first alone is over budget.
    assert len(search(indexed, embedder, "pet", token_budget=1)) == 1


def test_no_results(indexed: Conn, embedder: FakeEmbedder) -> None:
    assert search(indexed, embedder, "pet", sources=["nothing-here"]) == []


def test_vector_search_only_uses_the_same_embedding_model(
    indexed: Conn,
) -> None:
    class OtherModel(FakeEmbedder):
        @property
        def model_id(self) -> str:
            return "other:768"

    results = search(indexed, OtherModel(768), "refreshToken", limit=50)
    assert results  # keyword search still works...
    assert all(r.vector_rank is None for r in results)  # ...but no foreign-model vectors


def test_failed_reindex_keeps_last_good_chunks_searchable(
    indexed: Conn, embedder: FakeEmbedder
) -> None:
    with pytest.raises(Exception, match="No such file"):
        index_openapi(indexed, embedder, "/missing.json", "petstore", "1.0.27")
    status = indexed.execute("SELECT status FROM sources WHERE name = 'petstore'").fetchone()
    assert status == ("failed",)
    results = search(indexed, embedder, "find pet by id", sources=["petstore"], limit=50)
    assert "GET /pet/{petId}" in {r.path for r in results}


def test_user_text_cannot_break_the_sql(indexed: Conn, embedder: FakeEmbedder) -> None:
    for q in [
        "'; DROP TABLE chunks; --", "a & | ! <-> :*", "%(n)s %s", "\\", "",
        # FTS5 syntax: quotes, prefix *, NEAR, column filters, - and boolean words
        '"unbalanced', 'NEAR(pet store, 2)', "path:docs body:x", "-pet", "pet*", "AND OR NOT",
        "^start", "{path symbol}: pet",
    ]:  # fmt: skip
        search(indexed, embedder, q)
    assert count(indexed, "SELECT count(*) FROM chunks") > 0


def test_fts_query_quotes_every_term() -> None:
    assert fts_query("How do I refreshToken?") == (
        '"how" OR "do" OR "i" OR "refreshtoken" OR "refresh" OR "token"'
    )
    assert fts_query('NEAR(a, "b") -c path:*') == '"near" OR "a" OR "b" OR "c" OR "path"'
    assert fts_query("?!") is None


def test_index_docs_rejects_paths_outside_the_repo(
    conn: Conn, embedder: FakeEmbedder, tmp_path: Path
) -> None:
    shutil.copytree(FIXTURES / "docs_repo", tmp_path / "r")
    for bad in ["../", "/etc", "missing"]:
        with pytest.raises(Exception, match=r"--path|not found"):
            index_docs(conn, embedder, DOCS_URL, "v2", bad, fetcher=fake_fetcher(tmp_path / "r"))


# --- Review fixes ------------------------------------------------------------------------


def test_new_docs_version_reuses_unchanged_embeddings(conn: Conn, embedder: FakeEmbedder) -> None:
    fetch = fake_fetcher(FIXTURES / "docs_repo")
    first = index_docs(conn, embedder, DOCS_URL, "v1", "docs", fetcher=fetch)
    second = index_docs(conn, embedder, DOCS_URL, "v2", "docs", fetcher=fetch)
    assert second.embeddings_created == 0
    assert second.embeddings_reused == first.chunks_by_kind.total()


def test_new_api_version_reuses_unchanged_embeddings(conn: Conn, embedder: FakeEmbedder) -> None:
    first = index_openapi(conn, embedder, PETSTORE, "petstore", "1.0.27")
    second = index_openapi(conn, embedder, PETSTORE, "petstore", "1.0.28")
    assert second.embeddings_created == 0
    assert second.embeddings_reused == first.chunks_by_kind.total()


def test_search_uses_one_snapshot_even_if_a_reindex_lands_mid_search(
    indexed: Conn,
    embedder: FakeEmbedder,
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = hybrid.keyword_search

    def keyword_then_swap(*args: object, **kwargs: object) -> list[int]:
        ids = original(*args, **kwargs)  # type: ignore[arg-type]
        # What a concurrent re-index's atomic swap does: delete the old rows (and commit).
        with closing(connect(db_path)) as other:
            other.execute("DELETE FROM documents")
        return ids

    monkeypatch.setattr(hybrid, "keyword_search", keyword_then_swap)
    results = search(indexed, embedder, "refreshToken")
    assert results and results[0].symbol == "refreshToken"


# --- SQLite storage (spec 11) ------------------------------------------------------------


def test_schema_is_created_once_and_versioned(db_path: Path) -> None:
    with closing(connect(db_path)) as c:
        assert c.execute("PRAGMA user_version").fetchone() == (1,)
        assert c.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        c.execute("INSERT INTO sources (type, name, url, version) VALUES ('repo', 'a', 'u', 'v')")
    with closing(connect(db_path)) as c:  # reopening keeps the data; nothing re-applied
        assert count(c, "SELECT count(*) FROM sources") == 1
        c.execute("PRAGMA user_version = 99")
    with pytest.raises(Exception, match="newer than this Grepwise"):
        connect(db_path)


def test_reindex_replaces_keyword_rows_too(
    conn: Conn, embedder: FakeEmbedder, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "mini_repo", repo)
    index_repo(conn, embedder, REPO_URL, "v1", fetcher=fake_fetcher(repo))
    assert search(conn, embedder, "refreshToken", mode="keyword")[0].symbol == "refreshToken"

    (repo / "web" / "session.ts").unlink()  # the function is gone in the new index
    index_repo(conn, embedder, REPO_URL, "v1", fetcher=fake_fetcher(repo))
    assert count(conn, "SELECT count(*) FROM chunks_fts") == count(
        conn, "SELECT count(*) FROM chunks"
    )
    found = search(conn, embedder, "refreshToken", mode="keyword", limit=50)
    assert all(r.path != "web/session.ts" for r in found)


def test_chunks_without_vectors_are_still_found_by_keyword(
    indexed: Conn, embedder: FakeEmbedder
) -> None:
    indexed.execute("DELETE FROM embeddings")
    indexed.execute("UPDATE sources SET generation = generation + 1")  # drop cached matrices
    results = search(indexed, embedder, "refreshToken")
    assert results[0].symbol == "refreshToken"
    assert all(r.vector_rank is None for r in results)


def test_vector_cache_reloads_after_a_reindex(
    conn: Conn, embedder: FakeEmbedder, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "mini_repo", repo)
    index_repo(conn, embedder, REPO_URL, "v1", fetcher=fake_fetcher(repo))
    search(conn, embedder, "token", mode="vector")  # warms the cache
    (repo / "web" / "session.ts").unlink()
    index_repo(conn, embedder, REPO_URL, "v1", fetcher=fake_fetcher(repo))
    results = search(conn, embedder, "token", mode="vector", limit=50, token_budget=None)
    live = {row[0] for row in conn.execute("SELECT path FROM documents")}
    assert results and {r.path for r in results} <= live  # no ids from the old index


def test_modes_use_one_side_only(indexed: Conn, embedder: FakeEmbedder) -> None:
    before = embedder.calls
    keyword = search(indexed, embedder, "refreshToken", mode="keyword", limit=50)
    assert embedder.calls == before  # no query embedding in keyword mode
    assert keyword and all(r.vector_rank is None for r in keyword)
    vector = search(indexed, embedder, "refreshToken", mode="vector", limit=50)
    assert vector and all(r.keyword_rank is None for r in vector)


def test_search_works_while_another_connection_writes(
    indexed: Conn, embedder: FakeEmbedder, db_path: Path
) -> None:
    with closing(connect(db_path)) as writer:
        writer.execute("BEGIN IMMEDIATE")  # holds the write lock, uncommitted
        writer.execute("DELETE FROM documents")
        assert search(indexed, embedder, "refreshToken")[0].symbol == "refreshToken"
        writer.execute("ROLLBACK")


def test_vector_matrices_are_cached_per_database(
    indexed: Conn, embedder: FakeEmbedder, tmp_path: Path
) -> None:
    CACHE.clear()
    search(indexed, embedder, "pet", mode="vector")
    with closing(connect(tmp_path / "other.db")) as other:  # same source ids, other database
        assert search(other, embedder, "pet", mode="vector") == []


def test_two_processes_creating_the_database_at_once(tmp_path: Path) -> None:
    """Both see version 0; the second must notice the first finished while it waited."""
    import threading

    path, errors = tmp_path / "race.db", []

    def open_db() -> None:
        try:
            connect(path).close()
        except Exception as exc:  # pragma: no cover - the failure being tested for
            errors.append(exc)

    threads = [threading.Thread(target=open_db) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with closing(connect(path)) as c:
        assert c.execute("PRAGMA user_version").fetchone() == (1,)
