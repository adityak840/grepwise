"""Writing sources, documents and chunks to Postgres."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
import psycopg
from pgvector import HalfVector

from grepwise.ingest.embed import Vector
from grepwise.ingest.enrich import EnrichedChunk


@dataclass(frozen=True)
class DocumentRecord:
    path: str
    language: str | None
    line_count: int
    chunks: list[EnrichedChunk]


def begin_source(conn: psycopg.Connection, type_: str, name: str, url: str, version: str) -> int:
    """Create or reuse the `sources` row and mark it `indexing`. Returns its id."""
    row = conn.execute(
        """
        INSERT INTO sources (type, name, url, version, status)
        VALUES (%s, %s, %s, %s, 'indexing')
        ON CONFLICT (type, name, version) DO UPDATE
          SET url = EXCLUDED.url, status = 'indexing', error = NULL, updated_at = now()
        RETURNING id
        """,
        (type_, name, url, version),
    ).fetchone()
    assert row is not None
    return int(row[0])


def mark_failed(conn: psycopg.Connection, source_id: int, error: str) -> None:
    conn.execute(
        "UPDATE sources SET status = 'failed', error = %s, updated_at = now() WHERE id = %s",
        (error[:2000], source_id),
    )


def existing_embeddings(
    conn: psycopg.Connection, type_: str, name: str, model_id: str, hashes: Iterable[str]
) -> dict[str, Vector]:
    """Embeddings already stored for this source name (any version) by the same embedding
    model, keyed by content hash."""
    rows = conn.execute(
        """
        SELECT DISTINCT ON (c.content_hash) c.content_hash, c.embedding
        FROM chunks c JOIN sources s ON s.id = c.source_id
        WHERE s.type = %s AND s.name = %s AND c.embedding_model = %s
          AND c.content_hash = ANY(%s) AND c.embedding IS NOT NULL
        """,
        (type_, name, model_id, list(set(hashes))),
    ).fetchall()
    return {h: np.asarray(emb.to_numpy(), dtype=np.float32) for h, emb in rows}


def replace_content(
    conn: psycopg.Connection,
    source_id: int,
    commit_sha: str,
    documents: Sequence[DocumentRecord],
    embeddings: dict[str, Vector],
    embedding_model: str,
) -> None:
    """Swap in the new documents and chunks and mark the source `ready`, atomically."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM documents WHERE source_id = %s", (source_id,))  # cascades
        for doc in documents:
            cur.execute(
                """
                INSERT INTO documents (source_id, path, language, line_count)
                VALUES (%s, %s, %s, %s) RETURNING id
                """,
                (source_id, doc.path, doc.language, doc.line_count),
            )
            row = cur.fetchone()
            assert row is not None
            doc_id = row[0]
            cur.executemany(
                """
                INSERT INTO chunks (document_id, source_id, kind, symbol, start_line, end_line,
                                    header, content, content_hash, embedding, embedding_model)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        doc_id, source_id, e.chunk.kind, e.chunk.symbol,
                        e.chunk.start_line, e.chunk.end_line, e.header, e.chunk.content,
                        e.content_hash, HalfVector(embeddings[e.content_hash]), embedding_model,
                    )
                    for e in doc.chunks
                ],
            )  # fmt: skip
        cur.execute(
            """
            UPDATE sources SET status = 'ready', error = NULL, commit_sha = %s, updated_at = now()
            WHERE id = %s
            """,
            (commit_sha, source_id),
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


def source_stats(conn: psycopg.Connection) -> list[SourceStats]:
    rows = conn.execute(
        """
        SELECT s.name, s.type, s.version, s.status,
               (SELECT count(*) FROM documents d WHERE d.source_id = s.id),
               (SELECT count(*) FROM chunks c WHERE c.source_id = s.id),
               s.error
        FROM sources s ORDER BY s.name, s.version
        """
    ).fetchall()
    return [SourceStats(*row) for row in rows]


def database_size(conn: psycopg.Connection) -> str:
    row = conn.execute("SELECT pg_size_pretty(pg_database_size(current_database()))").fetchone()
    return str(row[0]) if row else "?"
