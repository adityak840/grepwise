"""Writing sources, documents, chunks and embeddings to SQLite."""

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from grepwise.db.connection import Conn, json_list, transaction
from grepwise.ingest.embed import Vector
from grepwise.ingest.enrich import EnrichedChunk

_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
_HASHES_PER_QUERY = 5_000  # keeps each `json_each` parameter a reasonable size


@dataclass(frozen=True)
class DocumentRecord:
    path: str
    language: str | None
    line_count: int
    content: str  # full text, for read_file
    chunks: list[EnrichedChunk]


def symbol_short(kind: str, symbol: str | None) -> str | None:
    """The name a person would type: 'Signer.sign' -> 'sign'; a docs section's last heading
    ('Options > Required' -> 'Required'); an endpoint stays whole ('GET /pet/{petId}')."""
    if symbol is None:
        return None
    if kind == "section":
        return symbol.rsplit(" > ", 1)[-1]
    if kind == "endpoint":
        return symbol
    return symbol.rsplit(".", 1)[-1]


def vector_to_blob(vector: Vector) -> bytes:
    return np.asarray(vector, dtype="<f2").tobytes()  # float16, little-endian


def blob_to_vector(blob: bytes) -> Vector:
    return np.frombuffer(blob, dtype="<f2").astype(np.float32)


def begin_source(
    conn: Conn,
    type_: str,
    name: str,
    url: str,
    version: str,
    subpath: str | None = None,
) -> int:
    """Create or reuse the `sources` row and mark it `indexing`. Returns its id."""
    row = conn.execute(
        f"""
        INSERT INTO sources (type, name, url, version, subpath, status)
        VALUES (?, ?, ?, ?, ?, 'indexing')
        ON CONFLICT (type, name, version) DO UPDATE
          SET url = excluded.url, subpath = excluded.subpath, status = 'indexing',
              error = NULL, updated_at = {_NOW}
        RETURNING id
        """,
        (type_, name, url, version, subpath),
    ).fetchone()
    assert row is not None
    return int(row[0])


def mark_failed(conn: Conn, source_id: int, error: str) -> None:
    conn.execute(
        f"UPDATE sources SET status = 'failed', error = ?, updated_at = {_NOW} WHERE id = ?",
        (error[:2000], source_id),
    )


def cached_embeddings(conn: Conn, model_id: str, hashes: Iterable[str]) -> dict[str, Vector]:
    """Embeddings this model has already made for these content hashes (from any source)."""
    wanted = sorted(set(hashes))
    found: dict[str, Vector] = {}
    for i in range(0, len(wanted), _HASHES_PER_QUERY):
        rows = conn.execute(
            "SELECT content_hash, vector FROM embeddings "
            "WHERE model_id = ? AND content_hash IN (SELECT value FROM json_each(?))",
            (model_id, json_list(wanted[i : i + _HASHES_PER_QUERY])),
        )
        found.update((h, blob_to_vector(blob)) for h, blob in rows)
    return found


def save_embeddings(conn: Conn, model_id: str, items: dict[str, Vector]) -> None:
    """Save new embeddings right away (their own transaction), so a later failure can't
    lose them."""
    with transaction(conn, immediate=True):
        conn.executemany(
            "INSERT OR IGNORE INTO embeddings (model_id, content_hash, vector) VALUES (?, ?, ?)",
            [(model_id, h, vector_to_blob(v)) for h, v in items.items()],
        )


def delete_content(conn: Conn, source_id: int) -> None:
    """Remove a source's documents and chunks, and their keyword-index rows. Call inside a
    transaction. Embeddings are kept: re-indexing reuses them."""
    conn.execute(
        "DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE source_id = ?)",
        (source_id,),
    )
    conn.execute("DELETE FROM documents WHERE source_id = ?", (source_id,))  # cascades to chunks


def replace_content(
    conn: Conn,
    source_id: int,
    commit_sha: str | None,
    documents: Sequence[DocumentRecord],
    embedding_model: str,
) -> None:
    """Swap in the new documents and chunks and mark the source `ready`, atomically."""
    with transaction(conn, immediate=True):
        delete_content(conn, source_id)
        # The write lock is held, so ids handed out here can't collide with another writer's.
        (next_id,) = conn.execute("SELECT coalesce(max(id), 0) + 1 FROM chunks").fetchone()
        for doc in documents:
            row = conn.execute(
                "INSERT INTO documents (source_id, path, language, line_count, content) "
                "VALUES (?, ?, ?, ?, ?) RETURNING id",
                (source_id, doc.path, doc.language, doc.line_count, doc.content),
            ).fetchone()
            assert row is not None
            doc_id = row[0]
            ids = range(next_id, next_id + len(doc.chunks))
            next_id += len(doc.chunks)
            conn.executemany(
                """
                INSERT INTO chunks (id, document_id, source_id, kind, symbol, symbol_short,
                                    start_line, end_line, header, content, content_hash,
                                    search_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        cid, doc_id, source_id, e.chunk.kind, e.chunk.symbol,
                        symbol_short(e.chunk.kind, e.chunk.symbol), e.chunk.start_line,
                        e.chunk.end_line, e.header, e.chunk.content, e.content_hash,
                        e.search_text,
                    )
                    for cid, e in zip(ids, doc.chunks, strict=True)
                ],
            )  # fmt: skip
            conn.executemany(
                "INSERT INTO chunks_fts (rowid, path, symbol, body) VALUES (?, ?, ?, ?)",
                [
                    (cid, doc.path, e.chunk.symbol or "", e.search_text)
                    for cid, e in zip(ids, doc.chunks, strict=True)
                ],
            )
        conn.execute(
            f"""
            UPDATE sources SET status = 'ready', error = NULL, commit_sha = ?,
                   embedding_model = ?, generation = ?, updated_at = {_NOW}
            WHERE id = ?
            """,
            (commit_sha, embedding_model, time.time_ns(), source_id),
        )


@dataclass(frozen=True)
class SourceStats:
    name: str
    type: str
    version: str
    status: str
    documents: int
    chunks: int
    error: str | None


def source_stats(conn: Conn, type_: str | None = None) -> list[SourceStats]:
    rows = conn.execute(
        """
        SELECT s.name, s.type, s.version, s.status,
               (SELECT count(*) FROM documents d WHERE d.source_id = s.id),
               (SELECT count(*) FROM chunks c WHERE c.source_id = s.id),
               s.error
        FROM sources s
        WHERE ?1 IS NULL OR s.type = ?1
        ORDER BY s.name, s.version
        """,
        (type_,),
    ).fetchall()
    return [SourceStats(*row) for row in rows]


def database_size(conn: Conn) -> str:
    (pages,) = conn.execute("PRAGMA page_count").fetchone()
    (page_size,) = conn.execute("PRAGMA page_size").fetchone()
    size = float(pages * page_size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")
