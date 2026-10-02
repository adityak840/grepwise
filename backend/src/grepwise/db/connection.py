"""Opening Postgres connections."""

import psycopg
from pgvector.psycopg import register_vector

from grepwise.config import get_settings


def connect(url: str | None = None, *, vectors: bool = True) -> psycopg.Connection:
    """Open an autocommit connection; wrap multi-statement work in `conn.transaction()`.

    `vectors=False` skips registering pgvector types, which fails until the
    `vector` extension exists (i.e. before the first migration).
    """
    conn = psycopg.connect(url or get_settings().database_url, autocommit=True)
    if vectors:
        register_vector(conn)
    return conn
