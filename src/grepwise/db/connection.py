"""Opening the SQLite database, creating its schema, and transactions.

One file (`GREPWISE_HOME/grepwise.db`) shared by every project and every MCP server process.
WAL mode lets readers keep searching while another process writes.
"""

import json
import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any

from grepwise.config import get_settings

Conn = sqlite3.Connection

# Schema steps, applied in order; `PRAGMA user_version` records how many have run.
SCHEMA_STEPS = ["schema.sql", "schema_2.sql", "schema_3.sql", "schema_4.sql", "schema_5.sql"]


class DatabaseError(Exception):
    pass


def connect(path: Path | str | None = None) -> Conn:
    """Open (creating if needed) the database and bring its schema up to date.

    Autocommit: each statement commits on its own unless wrapped in `transaction()`.
    `check_same_thread=False` because the MCP server runs tools in worker threads; callers
    serialise access themselves (see mcp/stdio.py).
    """
    target = Path(path) if path is not None else get_settings().db_path
    in_memory = str(target) == ":memory:"
    if not in_memory:
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, isolation_level=None, check_same_thread=False, timeout=5.0)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")  # wait up to 5 s for another writer
    if not in_memory:
        _enable_wal(conn)
        conn.execute("PRAGMA synchronous = NORMAL")  # safe with WAL, and much faster commits
    _check_fts5(conn)
    _apply_schema(conn)
    # A Grepwise from before schema step 5 that is still running (an MCP server started before
    # an upgrade) writes package sources without an ecosystem; they were all PyPI.
    if "schema_5.sql" in SCHEMA_STEPS:
        conn.execute(
            "UPDATE sources SET ecosystem = 'pypi' WHERE package IS NOT NULL AND ecosystem = ''"
        )
    return conn


def _enable_wal(conn: Conn, attempts: int = 100) -> None:
    """Switch to WAL once per file (the mode is stored in the file). Switching needs an
    exclusive lock, and SQLite may refuse it at once rather than wait for busy_timeout, e.g.
    when two MCP servers open a brand-new database together, so retry briefly."""
    for _ in range(attempts):
        try:
            (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
            if mode != "wal":
                conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) and "busy" not in str(exc):
                raise
            time.sleep(0.05)
    raise DatabaseError("The database stayed locked while switching it to WAL mode.")


def _check_fts5(conn: Conn) -> None:
    try:
        conn.execute("CREATE VIRTUAL TABLE temp.fts5_check USING fts5(x)")
        conn.execute("DROP TABLE temp.fts5_check")
    except sqlite3.OperationalError as exc:
        raise DatabaseError(
            f"This Python's SQLite ({sqlite3.sqlite_version}) has no FTS5, which keyword search "
            "needs. Use a Python built with FTS5, such as one installed by uv."
        ) from exc


def _apply_schema(conn: Conn) -> None:
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    if version > len(SCHEMA_STEPS):
        raise DatabaseError(
            f"The database is schema version {version}, newer than this Grepwise "
            f"({len(SCHEMA_STEPS)}). Upgrade Grepwise."
        )
    for step in range(version, len(SCHEMA_STEPS)):
        sql = resources.files("grepwise.db").joinpath(SCHEMA_STEPS[step]).read_text()
        # A step that rebuilds a table other tables point at runs with foreign keys off (the
        # pragma only changes outside a transaction), then checks them before turning them on.
        rebuilds = "-- needs: foreign_keys off" in sql
        if rebuilds:
            conn.execute("PRAGMA foreign_keys = OFF")
        try:
            with transaction(conn, immediate=True):
                # Another process (a second MCP server starting at the same moment) may have
                # applied this step while we waited for the write lock.
                (current,) = conn.execute("PRAGMA user_version").fetchone()
                if current > step:
                    continue
                for statement in _statements(sql):
                    conn.execute(statement)
                if rebuilds and conn.execute("PRAGMA foreign_key_check").fetchone():
                    raise DatabaseError(f"{SCHEMA_STEPS[step]} broke a foreign key.")
                conn.execute(f"PRAGMA user_version = {step + 1}")
        finally:
            if rebuilds:
                conn.execute("PRAGMA foreign_keys = ON")


def _statements(sql: str) -> Iterator[str]:
    """Split a schema file into statements (it has no ';' or '--' inside strings or triggers)."""
    lines = [line.split("--", 1)[0] for line in sql.splitlines()]
    for statement in "\n".join(lines).split(";"):
        if statement.strip():
            yield statement


@contextmanager
def transaction(conn: Conn, *, immediate: bool = False) -> Iterator[Conn]:
    """A transaction, or a savepoint inside an open one. In WAL mode a read transaction is a
    consistent snapshot. `immediate` takes the write lock up front, so a writer never fails
    halfway with 'database is locked'."""
    if conn.in_transaction:
        conn.execute("SAVEPOINT nested")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK TO nested")
            conn.execute("RELEASE nested")
            raise
        conn.execute("RELEASE nested")
        return
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def json_list(values: Sequence[Any] | None) -> str | None:
    """A list as one SQL parameter, read back with `json_each(?)`. None stays None ('any')."""
    return None if values is None else json.dumps(list(values))
