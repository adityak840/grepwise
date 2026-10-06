import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from grepwise.config import get_settings
from grepwise.db.connection import connect
from grepwise.db.migrate import MIGRATIONS_DIR, migrate
from grepwise.ingest.embed import FakeEmbedder
from grepwise.ingest.pipeline import index_docs, index_openapi, index_repo
from tests.helpers import DOCS_URL, PETSTORE, REPO_URL, fake_fetcher

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
        admin = psycopg.connect(url, autocommit=True, connect_timeout=10)
    except psycopg.OperationalError as exc:
        # Locally, skipping keeps unit tests usable without Docker. In CI (GREPWISE_REQUIRE_DB=1)
        # a missing database must fail loudly, or every database test would "pass" by skipping.
        if os.environ.get("GREPWISE_REQUIRE_DB") == "1":
            pytest.fail(f"Postgres not reachable and GREPWISE_REQUIRE_DB=1: {exc}")
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


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder(768)


@pytest.fixture
def indexed(conn: psycopg.Connection, embedder: FakeEmbedder) -> psycopg.Connection:
    """The mini repo, the docs fixture and the Petstore spec, indexed with FakeEmbedder."""
    index_repo(conn, embedder, REPO_URL, "v1", fetcher=fake_fetcher(FIXTURES / "mini_repo"))
    index_docs(conn, embedder, DOCS_URL, "v2", "docs", fetcher=fake_fetcher(FIXTURES / "docs_repo"))
    index_openapi(conn, embedder, PETSTORE, "petstore", "1.0.27")
    return conn
