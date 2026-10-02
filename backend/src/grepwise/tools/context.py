"""What every tool gets: a database connection, settings, and (lazily) an embedder."""

from dataclasses import dataclass, field

import psycopg

from grepwise.config import Settings
from grepwise.ingest.embed import Embedder, make_embedder


@dataclass
class ToolContext:
    conn: psycopg.Connection
    settings: Settings
    _embedder: Embedder | None = field(default=None, repr=False)

    @property
    def embedder(self) -> Embedder:
        """Created on first use: only `search` needs one, and loading a model is slow."""
        if self._embedder is None:
            self._embedder = make_embedder(self.settings)
        return self._embedder
