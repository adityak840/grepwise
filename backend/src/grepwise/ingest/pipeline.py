"""fetch -> filter -> split -> enrich -> embed -> store, for repos, docs folders and OpenAPI specs.

Every source type shares `_index`, which handles embedding reuse, the atomic swap and
marking the source `ready` or `failed`. Each `index_*` function only builds documents.
"""

import contextlib
import logging
import re
import time
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grepwise.db.connection import Conn
from grepwise.ingest import fetch, store
from grepwise.ingest.chunkers import split_lines
from grepwise.ingest.chunkers.code import chunk_file
from grepwise.ingest.chunkers.docs import DOCS_EXTENSIONS, chunk_docs, docs_format, sphinx_dirs
from grepwise.ingest.chunkers.openapi import chunk_endpoint, render_operations
from grepwise.ingest.embed import Embedder, Vector
from grepwise.ingest.enrich import EnrichedChunk, docs_header, endpoint_header, enrich
from grepwise.ingest.filters import SourceFile, collect_files
from grepwise.ingest.openapi import load_spec

Fetcher = Callable[[str, str], AbstractContextManager[fetch.Checkout]]
SpecLoader = Callable[[str], dict[str, Any]]

log = logging.getLogger(__name__)

# New embeddings are saved to the cache after every this-many, so a failure loses little.
EMBED_SAVE_EVERY = 64

# Names and versions end up in citations (`name@version:path:Lx-Ly`), so no '@' or ':'.
# '+' is allowed for local versions (PEP 440: '1.0+local').
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+-]*$")


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
    conn: Conn,
    embedder: Embedder,
    *,
    type_: str,
    name: str,
    url: str,
    version: str,
    subpath: str | None,
    build: Callable[[IndexSummary], list[store.DocumentRecord]],
    ref: str | None = None,
    package: str | None = None,
) -> IndexSummary:
    """Run `build`, then embed and store. On any error the source is marked `failed` and the
    error re-raised; previously indexed chunks stay untouched until a run succeeds."""
    started = time.monotonic()
    summary = IndexSummary(name=name, version=version)
    source_id = store.begin_source(
        conn, type_, name, url, version, subpath, ref=ref, package=package
    )
    try:
        documents = build(summary)
        all_chunks = [e for d in documents for e in d.chunks]
        summary.chunks_by_kind.update(e.chunk.kind for e in all_chunks)
        embeddings = store.cached_embeddings(
            conn, embedder.model_id, (e.content_hash for e in all_chunks)
        )
        # Embed each new hash once, even if the same chunk text appears in several files.
        todo = {
            e.content_hash: e.embedding_text for e in all_chunks if e.content_hash not in embeddings
        }
        summary.embeddings_reused = sum(1 for e in all_chunks if e.content_hash in embeddings)
        summary.embeddings_created = len(todo)
        _embed_and_save(conn, embedder, todo, embeddings)

        store.replace_content(
            conn, source_id, summary.commit_sha or None, documents, embedder.model_id
        )
    except BaseException as exc:
        # Don't let a failure to record the failure hide the original error.
        with contextlib.suppress(Exception):
            store.mark_failed(conn, source_id, f"{type(exc).__name__}: {exc}")
        raise
    summary.seconds = time.monotonic() - started
    return summary


def _embed_and_save(
    conn: Conn,
    embedder: Embedder,
    todo: dict[str, str],
    embeddings: dict[str, Vector],
) -> None:
    """Embed `todo` (hash -> text) in groups, saving each group to the cache as it completes."""
    items = list(todo.items())
    for i in range(0, len(items), EMBED_SAVE_EVERY):
        group = items[i : i + EMBED_SAVE_EVERY]
        hashes, texts = [h for h, _ in group], [t for _, t in group]
        vectors = dict(zip(hashes, embedder.embed(texts), strict=True))
        store.save_embeddings(conn, embedder.model_id, vectors)
        embeddings.update(vectors)
        log.info("Embedded %d/%d new chunks", min(i + EMBED_SAVE_EVERY, len(items)), len(items))


def build_documents(
    name: str, files: list[SourceFile], *, docs_only: bool = False
) -> list[store.DocumentRecord]:
    """Split each file the way that suits it: docs by heading (Markdown, reST, and Sphinx `.txt`
    files), code by symbol. `docs_only` drops everything that isn't docs."""
    sphinx = sphinx_dirs(f.path for f in files)
    documents = []
    for f in files:
        fmt = docs_format(f.path, sphinx)
        if fmt is not None:
            chunks = chunk_docs(f.path, f.text, fmt)
            enriched = [enrich(f.path, c, docs_header(name, f.path, c)) for c in chunks]
            documents.append(_document(f.path, f.text, fmt, enriched))
        elif not docs_only:
            language, code_chunks = chunk_file(f.path, f.text)
            enriched = [enrich(f.path, c) for c in code_chunks]
            documents.append(_document(f.path, f.text, language, enriched))
    return documents


def index_repo(
    conn: Conn,
    embedder: Embedder,
    url: str,
    ref: str,
    *,
    name: str | None = None,
    version: str | None = None,
    package: str | None = None,
    fetcher: Fetcher = fetch.checkout,
) -> IndexSummary:
    """Index a GitHub repo at `ref`: code split by symbol, docs by heading. By default the
    source is named `owner/repo` at version `ref`; a package source passes its own name and
    version (e.g. 'typer', '0.27.2' for the tag '0.27.2' or 'v0.27.2')."""
    repo = fetch.parse_github_url(url)
    fetch.validate_ref(ref)
    source_name = validate_label(name, "name") if name else repo.name
    source_version = validate_label(version, "version") if version else ref

    def build(summary: IndexSummary) -> list[store.DocumentRecord]:
        with fetcher(repo.url, ref) as co:  # the temp folder is gone after this block
            summary.commit_sha = co.commit_sha
            files = collect_files(co.path)
        summary.files_kept, summary.skipped = len(files.kept), files.skipped
        return build_documents(source_name, files.kept)

    return _index(
        conn, embedder, type_="repo", name=source_name, url=repo.url, version=source_version,
        subpath=None, build=build, ref=ref, package=package,
    )  # fmt: skip


def index_local(
    conn: Conn,
    embedder: Embedder,
    folder: Path,
    *,
    name: str | None = None,
    version: str | None = None,
) -> IndexSummary:
    """Index a folder on this machine, in place: named after the folder, versioned by its git
    commit (short), or 'local' when it isn't a git repo."""
    root = folder.expanduser().resolve()
    if not root.is_dir():
        raise fetch.FetchError(f"Not a folder: {folder}")
    commit = fetch.local_commit(root)
    source_name = validate_label(name or root.name, "name")
    source_version = validate_label(version or (commit[:12] if commit else "local"), "version")

    def build(summary: IndexSummary) -> list[store.DocumentRecord]:
        summary.commit_sha = commit or ""
        files = collect_files(root)
        summary.files_kept, summary.skipped = len(files.kept), files.skipped
        return build_documents(source_name, files.kept)

    return _index(
        conn, embedder, type_="repo", name=source_name, url=root.as_uri(),
        version=source_version, subpath=None, build=build, ref=commit or None,
    )  # fmt: skip


def index_docs(
    conn: Conn,
    embedder: Embedder,
    url: str,
    ref: str,
    path: str,
    *,
    name: str | None = None,
    fetcher: Fetcher = fetch.checkout,
) -> IndexSummary:
    """Index the docs (Markdown, MDX, reST) under `path` in a GitHub repo at `ref`."""
    repo = fetch.parse_github_url(url)
    fetch.validate_ref(ref)
    source_name = validate_label(name, "name") if name else repo.name
    subpath = path.strip("/")

    def build(summary: IndexSummary) -> list[store.DocumentRecord]:
        with fetcher(repo.url, ref) as co:
            summary.commit_sha = co.commit_sha
            # conf.py and .txt too: a Sphinx project's .txt files are reST (see docs_format).
            files = collect_files(co.path, subpath, (*DOCS_EXTENSIONS, ".txt", "conf.py"))
        documents = build_documents(source_name, files.kept, docs_only=True)
        summary.files_kept = len(documents)
        summary.skipped = files.skipped
        summary.skipped["other_type"] += len(files.kept) - len(documents)
        return documents

    return _index(
        conn, embedder, type_="docs", name=source_name, url=repo.url, version=ref,
        subpath=subpath, build=build, ref=ref,
    )  # fmt: skip


def index_openapi(
    conn: Conn,
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
