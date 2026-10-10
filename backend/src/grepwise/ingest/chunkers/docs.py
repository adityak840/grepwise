"""Which files are docs, and how to split them. Used by both the repo and the docs indexers,
so a library indexed once gets its code split by symbol and its docs split by heading."""

from collections.abc import Iterable
from pathlib import PurePosixPath

from grepwise.ingest.chunkers import Chunk
from grepwise.ingest.chunkers.markdown import chunk_markdown
from grepwise.ingest.chunkers.rst import chunk_rst

DOCS_EXTENSIONS = (".md", ".mdx", ".rst")


def sphinx_dirs(paths: Iterable[str]) -> list[str]:
    """Folders containing a Sphinx `conf.py` ('' for the repo root)."""
    return sorted(
        str(PurePosixPath(p).parent).removesuffix(".")
        for p in paths
        if PurePosixPath(p).name == "conf.py"
    )


def docs_format(path: str, sphinx: Iterable[str] = ()) -> str | None:
    """'markdown', 'rst', or None for files that aren't docs. Sphinx projects such as Django
    write reST in `.txt` files, so a `.txt` under a Sphinx folder counts as reST."""
    lower = path.lower()
    if lower.endswith((".md", ".mdx")):
        return "markdown"
    if lower.endswith(".rst"):
        return "rst"
    if lower.endswith(".txt") and any(d == "" or path.startswith(d + "/") for d in sphinx):
        return "rst"
    return None


def chunk_docs(path: str, text: str, fmt: str) -> list[Chunk]:
    return chunk_rst(path, text) if fmt == "rst" else chunk_markdown(path, text)
