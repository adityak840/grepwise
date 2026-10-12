"""Vector search: brute force in numpy over each source's vectors, cached in memory.

Exact (every chunk is scored), with no index to build or tune. One query over 20,000 chunks
is a 20,000 x 768 matrix-vector product: under a millisecond in float32. Vectors are stored
as float16 (half the disk) and converted once, when a source's matrix is first loaded; numpy
only uses its fast BLAS routines for float32 (float16 was 30x slower when measured).
"""

import threading
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from grepwise.db.connection import Conn
from grepwise.ingest.embed import Vector


@dataclass(frozen=True)
class SourceMatrix:
    generation: int
    ids: npt.NDArray[np.int64]  # chunk id of each row
    kinds: npt.NDArray[np.object_]  # chunk kind of each row
    vectors: npt.NDArray[np.float32]  # one normalized vector per row
    total_chunks: int  # including chunks without a vector yet

    @property
    def coverage(self) -> float:
        return len(self.ids) / self.total_chunks if self.total_chunks else 1.0


class VectorCache:
    """{(database, source id, model): SourceMatrix}, reloaded when the source's `generation`
    changes (every successful index sets a new one)."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, int, str], SourceMatrix] = {}
        self._lock = threading.Lock()

    def get(
        self, conn: Conn, source_id: int, generation: int, model_id: str, dim: int
    ) -> SourceMatrix:
        key = (_database_key(conn), source_id, model_id)
        with self._lock:
            cached = self._entries.get(key)
            if cached is None or cached.generation != generation:
                cached = _load(conn, source_id, generation, model_id, dim)
                self._entries[key] = cached
            return cached

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def _database_key(conn: Conn) -> str:
    """The database file, so caches of different databases (e.g. in tests) never mix."""
    row = conn.execute("PRAGMA database_list").fetchone()
    return str(row[2]) if row and row[2] else f"memory:{id(conn)}"


def _load(conn: Conn, source_id: int, generation: int, model_id: str, dim: int) -> SourceMatrix:
    rows = conn.execute(
        """
        SELECT c.id, c.kind, e.vector
        FROM chunks c JOIN embeddings e ON e.content_hash = c.content_hash AND e.model_id = ?
        WHERE c.source_id = ?
        ORDER BY c.id
        """,
        (model_id, source_id),
    ).fetchall()
    vectors = np.frombuffer(b"".join(r[2] for r in rows), dtype="<f2")
    (total,) = conn.execute(
        "SELECT count(*) FROM chunks WHERE source_id = ?", (source_id,)
    ).fetchone()
    return SourceMatrix(
        generation=generation,
        ids=np.array([r[0] for r in rows], dtype=np.int64),
        kinds=np.array([r[1] for r in rows], dtype=object),
        vectors=vectors.astype(np.float32).reshape(len(rows), dim),
        total_chunks=int(total),
    )


CACHE = VectorCache()

# Vector search waits until every source being searched has most of its vectors. Ranking only
# the few chunks that happen to be embedded would put noise first; and leaving out just the
# unfinished source would fill the vector list with the other sources' chunks, which fusion
# then ranks as highly as the keyword matches (a requests changelog first for a Playwright
# question).
MIN_COVERAGE = 0.9


def vector_search(
    conn: Conn,
    query: Vector,
    model_id: str,
    sources: Sequence[tuple[int, int]],  # (source id, generation)
    kinds: Sequence[str] | None,
    n: int,
    *,
    cache: VectorCache = CACHE,
) -> list[int]:
    """The `n` chunk ids closest to `query` (cosine similarity), best first; ties by id. Empty
    while any of the sources is under MIN_COVERAGE."""
    q = np.asarray(query, dtype=np.float32)
    matrices = [cache.get(conn, s, g, model_id, len(q)) for s, g in sources]
    if any(m.coverage < MIN_COVERAGE for m in matrices):
        return []
    all_ids: list[npt.NDArray[np.int64]] = []
    all_scores: list[npt.NDArray[np.float32]] = []
    for m in matrices:
        if len(m.ids) == 0:
            continue
        scores = m.vectors @ q  # vectors are unit length: dot product = cosine similarity
        keep = np.isin(m.kinds, list(kinds)) if kinds else np.ones(len(m.ids), dtype=bool)
        ids, scores = m.ids[keep], scores[keep]
        if len(ids) > n:  # the best n of this source, without sorting all of them
            top = np.argpartition(-scores, n)[:n]
            ids, scores = ids[top], scores[top]
        all_ids.append(ids)
        all_scores.append(scores)
    if not all_ids:
        return []
    ids, scores = np.concatenate(all_ids), np.concatenate(all_scores)
    order = np.lexsort((ids, -scores))[:n]  # by score descending, then id
    return [int(i) for i in ids[order]]
