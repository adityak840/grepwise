"""What every tool gets: a database connection, settings, (lazily) an embedder, the
background embedding worker if one runs, and the folder to look for the user's project in."""

from dataclasses import dataclass, field
from pathlib import Path

from grepwise.config import Settings
from grepwise.db.connection import Conn
from grepwise.ingest.embed import Embedder, make_embedder
from grepwise.worker import EmbeddingWorker


@dataclass
class ToolContext:
    conn: Conn
    settings: Settings
    _embedder: Embedder | None = field(default=None, repr=False)
    worker: EmbeddingWorker | None = None  # the MCP server's; None in the CLI and most tests
    cwd: Path = field(default_factory=Path.cwd)  # where to look for a project by default

    @property
    def embedder(self) -> Embedder:
        """Created on first use: only `search` needs one, and loading a model is slow."""
        if self._embedder is None:
            self._embedder = make_embedder(self.settings)
        return self._embedder
