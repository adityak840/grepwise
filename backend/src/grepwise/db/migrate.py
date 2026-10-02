"""A minimal migration runner: applies `migrations/*.sql` files in filename order."""

from pathlib import Path

import psycopg

# backend/src/grepwise/db/migrate.py -> backend/migrations
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"

# Any constant works; it just stops two runners from migrating at once.
_LOCK_ID = 7_310_001


def applied_versions(conn: psycopg.Connection) -> set[str]:
    exists = conn.execute("SELECT to_regclass('schema_migrations') IS NOT NULL").fetchone()
    if not exists or not exists[0]:
        return set()  # fresh database: 001_init creates the table
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def migrate(conn: psycopg.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every pending migration, each in its own transaction. Returns the versions applied."""
    applied: list[str] = []
    conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
    try:
        done = applied_versions(conn)
        for path in sorted(migrations_dir.glob("*.sql")):
            version = path.stem
            if version in done:
                continue
            with conn.transaction():
                conn.execute(path.read_bytes())  # bytes: the SQL is ours, not user input
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            applied.append(version)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))
    return applied
