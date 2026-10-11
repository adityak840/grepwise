from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest

from grepwise.db.connection import Conn, connect
from grepwise.ingest.embed import FakeEmbedder
from grepwise.ingest.pipeline import index_docs, index_openapi, index_repo
from tests.helpers import DOCS_URL, PETSTORE, REPO_URL, fake_fetcher

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "grepwise.db"


@pytest.fixture
def conn(db_path: Path) -> Iterator[Conn]:
    """A connection to a fresh database (schema created on open)."""
    with closing(connect(db_path)) as c:
        yield c


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder(768)


@pytest.fixture
def indexed(conn: Conn, embedder: FakeEmbedder) -> Conn:
    """The mini repo, the docs fixture and the Petstore spec, indexed with FakeEmbedder."""
    index_repo(conn, embedder, REPO_URL, "v1", fetcher=fake_fetcher(FIXTURES / "mini_repo"))
    index_docs(conn, embedder, DOCS_URL, "v2", "docs", fetcher=fake_fetcher(FIXTURES / "docs_repo"))
    index_openapi(conn, embedder, PETSTORE, "petstore", "1.0.27")
    return conn
