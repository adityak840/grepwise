"""Hybrid search, end to end on the test database (FakeEmbedder; no network)."""

import shutil
from pathlib import Path

import numpy as np
import psycopg
import pytest
from pgvector import HalfVector

from grepwise.db.connection import connect
from grepwise.db.migrate import MIGRATIONS_DIR, migrate
from grepwise.ingest.embed import FakeEmbedder
from grepwise.ingest.pipeline import index_docs, index_openapi, index_repo
from grepwise.search import hybrid
from grepwise.search.citations import parse_citation
from grepwise.search.hybrid import rrf_merge, search
from tests.helpers import FIXTURES, count, fake_fetcher

REPO_URL = "https://github.com/example/mini_repo"
DOCS_URL = "https://github.com/example/docs_repo"
PETSTORE = str(FIXTURES / "openapi" / "petstore.json")


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


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder(768)


@pytest.fixture
def indexed(conn: psycopg.Connection, embedder: FakeEmbedder) -> psycopg.Connection:
    index_repo(conn, embedder, REPO_URL, "v1", fetcher=fake_fetcher(FIXTURES / "mini_repo"))
    index_docs(conn, embedder, DOCS_URL, "v2", "docs", fetcher=fake_fetcher(FIXTURES / "docs_repo"))
    index_openapi(conn, embedder, PETSTORE, "petstore", "1.0.27")
    return conn


def test_docs_index(indexed: psycopg.Connection) -> None:
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


def test_openapi_index(indexed: psycopg.Connection) -> None:
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


def test_every_chunk_gets_search_text(indexed: psycopg.Connection) -> None:
    assert count(indexed, "SELECT count(*) FROM chunks WHERE search_text IS NULL") == 0
    st = indexed.execute("SELECT search_text FROM chunks WHERE symbol = 'refreshToken'").fetchone()
    assert st is not None and st[0].endswith("refresh token")


# --- Search ------------------------------------------------------------------------------


def test_exact_identifier_is_first_and_found_by_keyword(
    indexed: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    results = search(indexed, embedder, "refreshToken")
    assert results[0].symbol == "refreshToken"
    assert results[0].keyword_rank == 1
    assert results[0].citation == "example/mini_repo@v1:web/session.ts:L6-L9"


def test_split_words_find_camel_case_code(
    indexed: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    results = search(indexed, embedder, "refresh token")
    assert results[0].symbol == "refreshToken" and results[0].keyword_rank == 1


def test_english_stemming_matches_word_forms(
    indexed: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    results = search(indexed, embedder, "requiring option", kinds=["section"])
    assert results[0].symbol == "Tutorial > CLI Options > Required Options"


def test_citations_parse_back(indexed: psycopg.Connection, embedder: FakeEmbedder) -> None:
    for r in search(indexed, embedder, "pet", limit=20, token_budget=100_000):
        c = parse_citation(r.citation)
        assert (c.source, c.path, c.start_line, c.end_line) == (
            r.source, r.path, r.start_line, r.end_line,
        )  # fmt: skip


def test_source_filters(indexed: psycopg.Connection, embedder: FakeEmbedder) -> None:
    q = "find pet by id"
    by_name = search(indexed, embedder, q, sources=["petstore"], limit=50, token_budget=10**6)
    assert by_name and {r.source for r in by_name} == {"petstore@1.0.27"}
    # FakeEmbedder vectors are random, so only the keyword rank is meaningful here.
    assert next(r for r in by_name if r.keyword_rank == 1).path == "GET /pet/{petId}"

    by_version = search(indexed, embedder, q, sources=["petstore@1.0.27"], limit=50)
    assert {r.source for r in by_version} == {"petstore@1.0.27"}
    assert search(indexed, embedder, q, sources=["petstore@9.9.9"]) == []

    two = search(
        indexed, embedder, q, sources=["petstore", "example/mini_repo"], limit=50,
        token_budget=10**6,
    )  # fmt: skip
    assert {r.source for r in two} == {"petstore@1.0.27", "example/mini_repo@v1"}


def test_kind_filter(indexed: psycopg.Connection, embedder: FakeEmbedder) -> None:
    results = search(indexed, embedder, "option", kinds=["section"], limit=50)
    assert results and {r.kind for r in results} == {"section"}
    mixed = search(indexed, embedder, "pet", kinds=["endpoint", "class"], limit=50)
    assert {r.kind for r in mixed} <= {"endpoint", "class"}


def test_limit_and_token_budget(indexed: psycopg.Connection, embedder: FakeEmbedder) -> None:
    assert len(search(indexed, embedder, "pet", limit=3, token_budget=10**6)) == 3
    budgeted = search(indexed, embedder, "pet", limit=50, token_budget=500)
    assert sum(len(r.content) // 4 for r in budgeted) <= 500
    # Always at least one result, even when the first alone is over budget.
    assert len(search(indexed, embedder, "pet", token_budget=1)) == 1


def test_no_results(indexed: psycopg.Connection, embedder: FakeEmbedder) -> None:
    assert search(indexed, embedder, "pet", sources=["nothing-here"]) == []


def test_vector_search_only_uses_the_same_embedding_model(
    indexed: psycopg.Connection,
) -> None:
    class OtherModel(FakeEmbedder):
        @property
        def model_id(self) -> str:
            return "other:768"

    results = search(indexed, OtherModel(768), "refreshToken", limit=50)
    assert results  # keyword search still works...
    assert all(r.vector_rank is None for r in results)  # ...but no foreign-model vectors


def test_failed_reindex_keeps_last_good_chunks_searchable(
    indexed: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    with pytest.raises(Exception, match="No such file"):
        index_openapi(indexed, embedder, "/missing.json", "petstore", "1.0.27")
    status = indexed.execute("SELECT status FROM sources WHERE name = 'petstore'").fetchone()
    assert status == ("failed",)
    results = search(indexed, embedder, "find pet by id", sources=["petstore"], limit=50)
    assert "GET /pet/{petId}" in {r.path for r in results}


def test_user_text_cannot_break_the_sql(
    indexed: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    for q in ["'; DROP TABLE chunks; --", "a & | ! <-> :*", "%(n)s %s", "\\", ""]:
        search(indexed, embedder, q)
    assert count(indexed, "SELECT count(*) FROM chunks") > 0


def test_index_docs_rejects_paths_outside_the_repo(
    conn: psycopg.Connection, embedder: FakeEmbedder, tmp_path: Path
) -> None:
    shutil.copytree(FIXTURES / "docs_repo", tmp_path / "r")
    for bad in ["../", "/etc", "missing"]:
        with pytest.raises(Exception, match=r"--path|not found"):
            index_docs(conn, embedder, DOCS_URL, "v2", bad, fetcher=fake_fetcher(tmp_path / "r"))


# --- Migration on M1-era data ------------------------------------------------------------


def test_migration_003_on_m1_data_and_search_still_works(empty_db_url: str, tmp_path: Path) -> None:
    old = tmp_path / "migrations"
    old.mkdir()
    for name in ("001_init.sql", "002_embedding_model.sql"):
        shutil.copy(MIGRATIONS_DIR / name, old / name)
    with connect(empty_db_url, vectors=False) as raw:
        assert migrate(raw, old) == ["001_init", "002_embedding_model"]
    embedder = FakeEmbedder(768)
    with connect(empty_db_url) as c:
        # A row exactly as M1 wrote it: no search_text, no document content.
        src = c.execute(
            "INSERT INTO sources (type, name, url, version, status) "
            "VALUES ('repo', 'old/repo', 'https://github.com/old/repo', 'v1', 'ready') "
            "RETURNING id"
        ).fetchone()
        assert src is not None
        doc = c.execute(
            "INSERT INTO documents (source_id, path, line_count) VALUES (%s, 'a.py', 2) "
            "RETURNING id",
            (src[0],),
        ).fetchone()
        assert doc is not None
        vec = HalfVector(embedder.embed(["x"])[0].astype(np.float32))
        c.execute(
            "INSERT INTO chunks (document_id, source_id, kind, symbol, start_line, end_line, "
            "header, content, content_hash, embedding, embedding_model) VALUES "
            "(%s, %s, 'function', 'legacyHelper', 1, 2, 'File: a.py', "
            "'def legacyHelper():\n    pass\n', 'h', %s, %s)",
            (doc[0], src[0], vec, embedder.model_id),
        )
        assert migrate(c) == ["003_docs_search", "004_embedding_cache"]
        # 004 copies existing vectors into the cache, so nothing already paid for is lost.
        assert count(c, "SELECT count(*) FROM embedding_cache WHERE content_hash = 'h'") == 1
        results = search(c, embedder, "legacyHelper")
        assert results[0].symbol == "legacyHelper" and results[0].keyword_rank == 1
        assert count(c, "SELECT count(*) FROM chunks WHERE search_text IS NULL") == 1


# --- Review fixes ------------------------------------------------------------------------


def test_new_docs_version_reuses_unchanged_embeddings(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    fetch = fake_fetcher(FIXTURES / "docs_repo")
    first = index_docs(conn, embedder, DOCS_URL, "v1", "docs", fetcher=fetch)
    second = index_docs(conn, embedder, DOCS_URL, "v2", "docs", fetcher=fetch)
    assert second.embeddings_created == 0
    assert second.embeddings_reused == first.chunks_by_kind.total()


def test_new_api_version_reuses_unchanged_embeddings(
    conn: psycopg.Connection, embedder: FakeEmbedder
) -> None:
    first = index_openapi(conn, embedder, PETSTORE, "petstore", "1.0.27")
    second = index_openapi(conn, embedder, PETSTORE, "petstore", "1.0.28")
    assert second.embeddings_created == 0
    assert second.embeddings_reused == first.chunks_by_kind.total()


def test_search_uses_one_snapshot_even_if_a_reindex_lands_mid_search(
    indexed: psycopg.Connection,
    embedder: FakeEmbedder,
    empty_db_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = hybrid.keyword_search

    def keyword_then_swap(*args: object, **kwargs: object) -> list[int]:
        ids = original(*args, **kwargs)  # type: ignore[arg-type]
        # What a concurrent re-index's atomic swap does: delete the old rows (and commit).
        with connect(empty_db_url) as other:
            other.execute("DELETE FROM documents")
        return ids

    monkeypatch.setattr(hybrid, "keyword_search", keyword_then_swap)
    results = search(indexed, embedder, "refreshToken")
    assert results and results[0].symbol == "refreshToken"
