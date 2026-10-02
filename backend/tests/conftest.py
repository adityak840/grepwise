from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from grepwise.config import get_settings
from grepwise.db.connection import connect
from grepwise.db.migrate import MIGRATIONS_DIR, migrate

FIXTURES = Path(__file__).parent / "fixtures"
TEST_DB = "grepwise_test"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture
def empty_db_url() -> str:
    """URL of the `grepwise_test` database, wiped to an empty schema (no migrations)."""
    url = get_settings().database_url
    try:
        admin = psycopg.connect(url, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not reachable (run `make db`): {exc}")
    with admin:
        exists = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,))
        if not exists.fetchone():
            admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    test_url = make_conninfo(url, dbname=TEST_DB)
    with connect(test_url, vectors=False) as raw:
        raw.execute("DROP SCHEMA public CASCADE")
        raw.execute("CREATE SCHEMA public")
    return test_url


@pytest.fixture
def conn(empty_db_url: str) -> Iterator[psycopg.Connection]:
    """A connection to a freshly migrated test database."""
    with connect(empty_db_url, vectors=False) as raw:
        assert migrate(raw) == sorted(p.stem for p in MIGRATIONS_DIR.glob("*.sql"))
        assert migrate(raw) == []  # idempotent
    with connect(empty_db_url) as c:
        yield c
