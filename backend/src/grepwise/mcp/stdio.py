"""`grepwise mcp`: the MCP server over stdio, for a local client (Claude Code, Cursor).

stdout belongs to the protocol. Logging goes to stderr, and the SDK points file descriptor 1
at stderr while serving, so even a stray print can't corrupt the stream. Anything that might
print (loading the embedding model) happens inside that window.
"""

import contextlib
import logging
import threading
from collections.abc import Iterator

import anyio
import anyio.to_thread
import psycopg
from mcp.server.stdio import stdio_server

from grepwise.config import Settings
from grepwise.db.connection import connect
from grepwise.ingest.embed import Embedder, LocalEmbedder, make_embedder
from grepwise.mcp.server import build_server
from grepwise.tools import ToolContext

log = logging.getLogger(__name__)


class SharedConnection:
    """One database connection for the session, reconnected if it drops. A lock serialises
    tool calls, since a connection can't run two queries at once."""

    def __init__(self, settings: Settings, embedder: Embedder):
        self.settings = settings
        self.embedder = embedder
        self._conn: psycopg.Connection | None = None
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def __call__(self) -> Iterator[ToolContext]:
        with self._lock:
            if self._conn is None or self._conn.closed or self._conn.broken:
                self._conn = connect(self.settings.database_url)
            try:
                yield ToolContext(self._conn, self.settings, self.embedder)
            except psycopg.OperationalError:
                self._conn.close()  # reconnect on the next call
                self._conn = None
                raise

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()


def warm_up(embedder: Embedder) -> None:
    if isinstance(embedder, LocalEmbedder):
        embedder.embed_query("warm up")  # so the first search isn't slow


def serve_stdio(settings: Settings) -> None:
    embedder = make_embedder(settings)
    shared = SharedConnection(settings, embedder)
    server = build_server(shared)

    async def main() -> None:
        async with stdio_server() as (read, write), anyio.create_task_group() as tg:
            tg.start_soon(anyio.to_thread.run_sync, warm_up, embedder)
            await server.run(read, write, server.create_initialization_options())
            tg.cancel_scope.cancel()  # the client closed the connection: don't wait for warm-up

    try:
        anyio.run(main)
    finally:
        shared.close()
