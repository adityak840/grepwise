"""fetch -> filter -> split -> enrich -> embed -> store, for repos, docs folders and OpenAPI specs.

Every source type shares `_index`, which handles embedding reuse, the atomic swap and
marking the source `ready` or `failed`. Each `index_*` function only builds documents.
"""

import contextlib
import re
import time
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any

import psycopg

from grepwise.ingest import fetch, store
from grepwise.ingest.chunkers import split_lines
from grepwise.ingest.chunkers.code import chunk_file
from grepwise.ingest.chunkers.markdown import DOCS_EXTENSIONS, chunk_markdown
from grepwise.ingest.chunkers.openapi import chunk_endpoint, render_operations
from grepwise.ingest.embed import Embedder
from grepwise.ingest.enrich import EnrichedChunk, docs_header, endpoint_header, enrich
from grepwise.ingest.filters import collect_files
from grepwise.ingest.openapi import load_spec

Fetcher = Callable[[str, str], AbstractContextManager[fetch.Checkout]]
SpecLoader = Callable[[str], dict[str, Any]]

# Names and versions end up in citations (`name@version:path:Lx-Ly`), so no '@' or ':'.
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class InvalidNameError(ValueError):
    pass


def validate_label(value: str, what: str) -> str:
    if not _SAFE_LABEL.match(value) or ".." in value:
        raise InvalidNameError(f"Invalid {what} {value!r}: use letters, digits and . _ / -")
    return value


@dataclass
class IndexSummary:
    name: str
    version: str
    commit_sha: str = ""
    files_kept: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    chunks_by_kind: Counter[str] = field(default_factory=Counter)
    embeddings_created: int = 0
    embeddings_reused: int = 0
    seconds: float = 0.0


def _document(
    path: str, text: str, language: str | None, chunks: list[EnrichedChunk]
) -> store.DocumentRecord:
    return store.DocumentRecord(path, language, len(split_lines(text)), text, chunks)


def _index(
    conn: psycopg.Connection,
    embedder: Embedder,
    *,
    type_: str,
    name: str,
    url: str,
    version: str,
    subpath: str | None,
    build: Callable[[IndexSummary], list[store.DocumentRecord]],
) -> IndexSummary:
    """Run `build`, then embed and store. On any error the source is marked `failed` and the
    error re-raised; previously indexed chunks stay untouched until a run succeeds."""
    started = time.monotonic()
    summary = IndexSummary(name=name, version=version)
    source_id = store.begin_source(conn, type_, name, url, version, subpath)
    try:
        documents = build(summary)
        all_chunks = [e for d in documents for e in d.chunks]
        summary.chunks_by_kind.update(e.chunk.kind for e in all_chunks)
        embeddings = store.existing_embeddings(
            conn, type_, name, embedder.model_id, (e.content_hash for e in all_chunks)
        )
        # Embed each new hash once, even if the same chunk text appears in several files.
        todo = {
            e.content_hash: e.embedding_text for e in all_chunks if e.content_hash not in embeddings
        }
        summary.embeddings_reused = sum(1 for e in all_chunks if e.content_hash in embeddings)
        summary.embeddings_created = len(todo)
        embeddings.update(zip(todo, embedder.embed(list(todo.values())), strict=True))

        store.replace_content(
            conn, source_id, summary.commit_sha or None, documents, embeddings, embedder.model_id
        )
    except BaseException as exc:
        # Don't let a failure to record the failure hide the original error.
        with contextlib.suppress(Exception):
            store.mark_failed(conn, source_id, f"{type(exc).__name__}: {exc}")
        raise
    summary.seconds = time.monotonic() - started
    return summary


def index_repo(
    conn: psycopg.Connection,
    embedder: Embedder,
    url: str,
    ref: str,
    *,
    fetcher: Fetcher = fetch.checkout,
) -> IndexSummary:
    """Index a GitHub repo's code at `ref`."""
    repo = fetch.parse_github_url(url)
    fetch.validate_ref(ref)

    def build(summary: IndexSummary) -> list[store.DocumentRecord]:
        with fetcher(repo.url, ref) as co:  # the temp folder is gone after this block
            summary.commit_sha = co.commit_sha
            files = collect_files(co.path)
        summary.files_kept, summary.skipped = len(files.kept), files.skipped
        documents = []
        for f in files.kept:
            language, chunks = chunk_file(f.path, f.text)
            documents.append(
                _document(f.path, f.text, language, [enrich(f.path, c) for c in chunks])
            )
        return documents

    return _index(
        conn, embedder, type_="repo", name=repo.name, url=repo.url, version=ref, subpath=None,
        build=build,
    )  # fmt: skip


def index_docs(
    conn: psycopg.Connection,
    embedder: Embedder,
    url: str,
    ref: str,
    path: str,
    *,
    name: str | None = None,
    fetcher: Fetcher = fetch.checkout,
) -> IndexSummary:
    """Index the Markdown/MDX files under `path` in a GitHub repo at `ref`."""
    repo = fetch.parse_github_url(url)
    fetch.validate_ref(ref)
    source_name = validate_label(name, "name") if name else repo.name
    subpath = path.strip("/")

    def build(summary: IndexSummary) -> list[store.DocumentRecord]:
        with fetcher(repo.url, ref) as co:
            summary.commit_sha = co.commit_sha
            files = collect_files(co.path, subpath, DOCS_EXTENSIONS)
        summary.files_kept, summary.skipped = len(files.kept), files.skipped
        documents = []
        for f in files.kept:
            chunks = chunk_markdown(f.path, f.text)
            enriched = [enrich(f.path, c, docs_header(source_name, f.path, c)) for c in chunks]
            documents.append(_document(f.path, f.text, "markdown", enriched))
        return documents

    return _index(
        conn, embedder, type_="docs", name=source_name, url=repo.url, version=ref,
        subpath=subpath, build=build,
    )  # fmt: skip


def index_openapi(
    conn: psycopg.Connection,
    embedder: Embedder,
    location: str,
    name: str,
    version: str,
    *,
    loader: SpecLoader = load_spec,
) -> IndexSummary:
    """Index an OpenAPI 3.x spec (URL or file): one virtual document per operation."""
    validate_label(name, "name")
    validate_label(version, "version")

    def build(summary: IndexSummary) -> list[store.DocumentRecord]:
        endpoints = render_operations(loader(location))
        summary.files_kept = len(endpoints)
        documents = []
        for ep in endpoints:
            chunk = chunk_endpoint(ep)
            enriched = [enrich(ep.path, chunk, endpoint_header(name, chunk))]
            documents.append(_document(ep.path, ep.content, None, enriched))
        return documents

    return _index(
        conn, embedder, type_="openapi", name=name, url=location, version=version, subpath=None,
        build=build,
    )  # fmt: skip
