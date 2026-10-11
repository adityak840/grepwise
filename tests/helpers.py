"""Shared helpers for the database tests."""

from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from grepwise.db.connection import Conn
from grepwise.ingest.fetch import Checkout

FIXTURES = Path(__file__).parent / "fixtures"
REPO_URL = "https://github.com/example/mini_repo"
DOCS_URL = "https://github.com/example/docs_repo"
PETSTORE = str(FIXTURES / "openapi" / "petstore.json")


def fake_fetcher(
    root: Path, sha: str = "a" * 40
) -> Callable[[str, str], AbstractContextManager[Checkout]]:
    """A fetcher that 'clones' a local folder instead of GitHub."""

    @contextmanager
    def fetch(url: str, ref: str) -> Generator[Checkout]:
        yield Checkout(path=root, commit_sha=sha)

    return fetch


def count(conn: Conn, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    assert row is not None
    return int(row[0])
