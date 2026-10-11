"""Background embedding in the MCP server process (spec 13, section 4).

On-demand indexing stores a library's chunks without vectors, so keyword search works at
once; this worker fills the vectors in. A lease in the `sources` row makes sure only one
process embeds a source at a time, even with several Claude Code sessions open.
"""

import logging
import threading
from contextlib import closing
from pathlib import Path

from grepwise.db.connection import Conn, connect
from grepwise.ingest.embed import Embedder
from grepwise.ingest.pipeline import embed_missing

log = logging.getLogger(__name__)

LEASE_SECONDS = 120
_LEASE = f"strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '+{LEASE_SECONDS} seconds')"
_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"


def take_lease(conn: Conn, source_id: int) -> bool:
    """Claim (or renew) the right to embed this source. False if another process holds it."""
    cur = conn.execute(
        f"UPDATE sources SET embed_lease_until = {_LEASE} "
        f"WHERE id = ? AND (embed_lease_until IS NULL OR embed_lease_until < {_NOW})",
        (source_id,),
    )
    return cur.rowcount == 1


def renew_lease(conn: Conn, source_id: int) -> None:
    conn.execute(f"UPDATE sources SET embed_lease_until = {_LEASE} WHERE id = ?", (source_id,))


def release_lease(conn: Conn, source_id: int) -> None:
    conn.execute("UPDATE sources SET embed_lease_until = NULL WHERE id = ?", (source_id,))


def sources_missing_embeddings(conn: Conn, model_id: str) -> list[int]:
    rows = conn.execute(
        """
        SELECT DISTINCT c.source_id
        FROM chunks c
        LEFT JOIN embeddings e ON e.content_hash = c.content_hash AND e.model_id = ?
        WHERE e.content_hash IS NULL
        """,
        (model_id,),
    )
    return sorted(r[0] for r in rows)


def embed_source(conn: Conn, embedder: Embedder, source_id: int, stop: threading.Event) -> int:
    """Embed one source's missing chunks under a lease. 0 if another process holds it."""
    if not take_lease(conn, source_id):
        return 0
    try:

        def keep_going() -> bool:
            renew_lease(conn, source_id)
            return not stop.is_set()

        return embed_missing(conn, embedder, source_id, keep_going=keep_going)
    finally:
        release_lease(conn, source_id)


class EmbeddingWorker:
    """A daemon thread embedding queued sources one at a time, with its own connection."""

    def __init__(self, db_path: Path, embedder: Embedder):
        self.db_path = db_path
        self.embedder = embedder
        self._queue: list[int] = []
        self._wake = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def enqueue(self, source_id: int) -> None:
        with self._wake:
            if source_id not in self._queue:
                self._queue.append(source_id)
            self._wake.notify()

    def enqueue_unfinished(self) -> None:
        """Queue every source with missing vectors, e.g. after a restart cut embedding short."""
        with closing(connect(self.db_path)) as conn:
            for source_id in sources_missing_embeddings(conn, self.embedder.model_id):
                self.enqueue(source_id)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="grepwise-embed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._wake:
            self._wake.notify()

    def run_until_idle(self) -> None:
        """Process the queue in the calling thread (for tests and the CLI)."""
        with closing(connect(self.db_path)) as conn:
            while (source_id := self._next(block=False)) is not None:
                embed_source(conn, self.embedder, source_id, self._stop)

    def _next(self, *, block: bool) -> int | None:
        with self._wake:
            while not self._queue and block and not self._stop.is_set():
                self._wake.wait()
            return self._queue.pop(0) if self._queue and not self._stop.is_set() else None

    def _run(self) -> None:
        with closing(connect(self.db_path)) as conn:
            while (source_id := self._next(block=True)) is not None:
                try:
                    n = embed_source(conn, self.embedder, source_id, self._stop)
                    if n:
                        log.info("Embedded %d chunks of source %d", n, source_id)
                except Exception:  # keep the worker alive for the next source
                    log.exception("Background embedding of source %d failed", source_id)
