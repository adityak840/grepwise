"""Indexing end to end on a fresh SQLite database, with FakeEmbedder. No network."""

import shutil
from pathlib import Path

import numpy as np
import pytest

from grepwise.db.connection import Conn
from grepwise.ingest.chunkers import split_lines
from grepwise.ingest.embed import DOCUMENT, FakeEmbedder
from grepwise.ingest.pipeline import index_repo
from grepwise.ingest.store import blob_to_vector
from tests.helpers import FIXTURES, count, fake_fetcher

pytestmark = pytest.mark.integration

URL = "https://github.com/example/mini_repo"


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


def test_index_reindex_and_reuse(conn: Conn, repo: Path) -> None:
    embedder = FakeEmbedder(768)
    first = index_repo(conn, embedder, URL, "v1", fetcher=fake_fetcher(repo))

    assert first.files_kept == 5
    assert first.skipped == {"skipped_dir": 1, "lockfile": 1, "binary": 1}
    n_chunks = first.chunks_by_kind.total()
    assert first.embeddings_created == n_chunks and first.embeddings_reused == 0
    assert count(conn, "SELECT count(*) FROM sources WHERE status = 'ready'") == 1
    assert count(conn, "SELECT count(*) FROM documents") == 5
    assert count(conn, "SELECT count(*) FROM chunks") == n_chunks
    assert count(conn, "SELECT count(*) FROM chunks WHERE symbol = 'Signer.sign'") == 0  # small
    assert count(conn, "SELECT count(*) FROM chunks WHERE symbol = 'Signer'") == 1
    assert count(conn, "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'hmac'") > 0
    assert count(conn, "SELECT count(*) FROM chunks_fts") == n_chunks

    # Stored vectors are unit length (float16 rounding stays well inside 0.001).
    assert count(conn, "SELECT count(*) FROM embeddings") == n_chunks
    for (blob,) in conn.execute("SELECT vector FROM embeddings"):
        assert abs(float(np.linalg.norm(blob_to_vector(blob))) - 1) < 1e-3

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


def test_failure_marks_source_failed_and_keeps_old_chunks(conn: Conn, repo: Path) -> None:
    index_repo(conn, FakeEmbedder(768), URL, "v1", fetcher=fake_fetcher(repo))
    n_chunks = count(conn, "SELECT count(*) FROM chunks")

    class BrokenEmbedder(FakeEmbedder):
        def _embed_batch(self, texts: list[str], task: str = "") -> list[list[float]]:
            raise RuntimeError("quota exhausted")

    (repo / "new.py").write_text("def brand_new():\n    return 42\n")
    with pytest.raises(RuntimeError):
        index_repo(conn, BrokenEmbedder(768), URL, "v1", fetcher=fake_fetcher(repo))
    row = conn.execute("SELECT status, error FROM sources").fetchone()
    assert row == ("failed", "RuntimeError: quota exhausted")
    assert count(conn, "SELECT count(*) FROM chunks") == n_chunks


def test_switching_embedding_model_re_embeds_everything(conn: Conn, repo: Path) -> None:
    first = index_repo(conn, FakeEmbedder(768), URL, "v1", fetcher=fake_fetcher(repo))
    n_chunks = first.chunks_by_kind.total()

    class OtherModel(FakeEmbedder):
        @property
        def model_id(self) -> str:
            return "other-model:768"

    second = index_repo(conn, OtherModel(768), URL, "v1", fetcher=fake_fetcher(repo))
    assert second.embeddings_reused == 0 and second.embeddings_created == n_chunks
    models = {m for (m,) in conn.execute("SELECT embedding_model FROM sources")}
    assert models == {"other-model:768"}

    # Back to the first model: its vectors are still in the embedding cache, so switching back
    # costs nothing.
    third = index_repo(conn, FakeEmbedder(768), URL, "v2", fetcher=fake_fetcher(repo))
    assert third.embeddings_created == 0 and third.embeddings_reused == n_chunks


def test_a_failed_run_keeps_the_embeddings_it_made(
    conn: Conn, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grepwise.ingest import pipeline

    monkeypatch.setattr(pipeline, "EMBED_SAVE_EVERY", 2)  # save after every 2 new embeddings

    class DiesOnSecondGroup(FakeEmbedder):
        def _embed_batch(self, texts: list[str], task: str = DOCUMENT) -> list[list[float]]:
            if self.calls >= 1:
                raise RuntimeError("quota exhausted")
            return super()._embed_batch(texts, task)

    with pytest.raises(RuntimeError):
        index_repo(conn, DiesOnSecondGroup(768), URL, "v1", fetcher=fake_fetcher(repo))
    saved = count(conn, "SELECT count(*) FROM embeddings")
    assert saved == 2

    rerun = index_repo(conn, FakeEmbedder(768), URL, "v1", fetcher=fake_fetcher(repo))
    assert rerun.embeddings_reused == 2
    assert rerun.embeddings_created == rerun.chunks_by_kind.total() - 2
