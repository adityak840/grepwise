"""End to end against the Docker Postgres (`make db`), with FakeEmbedder. No network.

Uses a separate `grepwise_test` database, rebuilt from scratch for each test.
"""

import shutil
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from grepwise.config import get_settings
from grepwise.db.connection import connect
from grepwise.db.migrate import migrate
from grepwise.ingest.chunkers import split_lines
from grepwise.ingest.embed import FakeEmbedder
from grepwise.ingest.fetch import Checkout
from grepwise.ingest.pipeline import index_repo

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://github.com/example/mini_repo"
TEST_DB = "grepwise_test"


def _test_db_url() -> str:
    url = get_settings().database_url
    try:
        admin = psycopg.connect(url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not reachable (run `make db`): {exc}")
    with admin:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,))
        if not exists.fetchone():
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    return make_conninfo(url, dbname=TEST_DB)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    url = _test_db_url()
    with connect(url, vectors=False) as raw:
        raw.execute("DROP SCHEMA public CASCADE")
        raw.execute("CREATE SCHEMA public")
        assert migrate(raw) == ["001_init", "002_embedding_model"]
        assert migrate(raw) == []  # idempotent
    with connect(url) as c:
        yield c


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The mini repo plus junk that the filters must skip."""
    root = tmp_path / "mini_repo"
    shutil.copytree(FIXTURES / "mini_repo", root)
    (root / "node_modules" / "dep").mkdir(parents=True)
    (root / "node_modules" / "dep" / "index.js").write_text("module.exports = 1;\n")
    (root / "package-lock.json").write_text("{}\n")
    (root / "logo.png").write_bytes(b"\x89PNG\x00\x00")
    return root


def fake_fetcher(root: Path, sha: str = "a" * 40):  # type: ignore[no-untyped-def]
    @contextmanager
    def fetch(url: str, ref: str) -> Generator[Checkout]:
        yield Checkout(path=root, commit_sha=sha)

    return fetch


def count(conn: psycopg.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    assert row is not None
    return int(row[0])


def test_index_reindex_and_reuse(conn: psycopg.Connection, repo: Path) -> None:
    embedder = FakeEmbedder(768)
    first = index_repo(conn, embedder, URL, "v1", fetcher=fake_fetcher(repo))

    assert first.files_kept == 4
    assert first.skipped == {"skipped_dir": 1, "lockfile": 1, "binary": 1}
    n_chunks = first.chunks_by_kind.total()
    assert first.embeddings_created == n_chunks and first.embeddings_reused == 0
    assert count(conn, "SELECT count(*) FROM sources WHERE status = 'ready'") == 1
    assert count(conn, "SELECT count(*) FROM documents") == 4
    assert count(conn, "SELECT count(*) FROM chunks") == n_chunks
    assert count(conn, "SELECT count(*) FROM chunks WHERE symbol = 'Signer.sign'") == 0  # small
    assert count(conn, "SELECT count(*) FROM chunks WHERE symbol = 'Signer'") == 1
    assert count(conn, "SELECT count(*) FROM chunks WHERE tsv @@ to_tsquery('simple', 'hmac')") > 0

    # Stored vectors are unit length (halfvec rounding stays well inside 0.001).
    for (emb,) in conn.execute("SELECT embedding FROM chunks"):
        assert abs(float(np.linalg.norm(emb.to_numpy().astype(np.float32))) - 1) < 1e-3

    # Stored content matches the file lines exactly.
    for path, start, end, content in conn.execute(
        "SELECT d.path, c.start_line, c.end_line, c.content FROM chunks c "
        "JOIN documents d ON d.id = c.document_id"
    ):
        lines = split_lines((repo / path).read_text())
        assert content == "".join(lines[start - 1 : end])

    # Re-index the same version: rows replaced, nothing re-embedded.
    calls_before = embedder.texts_embedded
    second = index_repo(conn, embedder, URL, "v1", fetcher=fake_fetcher(repo, "b" * 40))
    assert second.embeddings_created == 0 and second.embeddings_reused == n_chunks
    assert embedder.texts_embedded == calls_before
    assert count(conn, "SELECT count(*) FROM chunks") == n_chunks
    assert count(conn, "SELECT count(*) FROM sources") == 1
    sha = conn.execute("SELECT commit_sha FROM sources").fetchone()
    assert sha == ("b" * 40,)

    # A new version reuses embeddings for unchanged chunks, embeds only the changed one.
    signer = repo / "src" / "minipkg" / "signer.py"
    signer.write_text(signer.read_text().replace("hexdigest()", "digest().hex()"))
    third = index_repo(conn, embedder, URL, "v2", fetcher=fake_fetcher(repo))
    assert third.embeddings_created == 1
    assert third.embeddings_reused == third.chunks_by_kind.total() - 1
    assert count(conn, "SELECT count(*) FROM sources WHERE status = 'ready'") == 2


def test_failure_marks_source_failed_and_keeps_old_chunks(
    conn: psycopg.Connection, repo: Path
) -> None:
    index_repo(conn, FakeEmbedder(768), URL, "v1", fetcher=fake_fetcher(repo))
    n_chunks = count(conn, "SELECT count(*) FROM chunks")

    class BrokenEmbedder(FakeEmbedder):
        def _embed_batch(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("quota exhausted")

    (repo / "new.py").write_text("def brand_new():\n    return 42\n")
    with pytest.raises(RuntimeError):
        index_repo(conn, BrokenEmbedder(768), URL, "v1", fetcher=fake_fetcher(repo))
    row = conn.execute("SELECT status, error FROM sources").fetchone()
    assert row == ("failed", "RuntimeError: quota exhausted")
    assert count(conn, "SELECT count(*) FROM chunks") == n_chunks


def test_switching_embedding_model_re_embeds_everything(
    conn: psycopg.Connection, repo: Path
) -> None:
    first = index_repo(conn, FakeEmbedder(768), URL, "v1", fetcher=fake_fetcher(repo))
    n_chunks = first.chunks_by_kind.total()

    class OtherModel(FakeEmbedder):
        @property
        def model_id(self) -> str:
            return "other-model:768"

    second = index_repo(conn, OtherModel(768), URL, "v1", fetcher=fake_fetcher(repo))
    assert second.embeddings_reused == 0 and second.embeddings_created == n_chunks
    models = {m for (m,) in conn.execute("SELECT DISTINCT embedding_model FROM chunks")}
    assert models == {"other-model:768"}

    # Back to the first model: its old vectors were replaced, so nothing is reused either.
    third = index_repo(conn, FakeEmbedder(768), URL, "v2", fetcher=fake_fetcher(repo))
    assert third.embeddings_reused == 0
