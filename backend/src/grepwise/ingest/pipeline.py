"""fetch -> filter -> split -> enrich -> embed -> store, for one repo at one ref."""

import contextlib
import time
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field

import psycopg

from grepwise.ingest import fetch, store
from grepwise.ingest.chunkers import split_lines
from grepwise.ingest.chunkers.code import chunk_file
from grepwise.ingest.embed import Embedder
from grepwise.ingest.enrich import enrich
from grepwise.ingest.filters import collect_files

Fetcher = Callable[[str, str], AbstractContextManager[fetch.Checkout]]


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


def index_repo(
    conn: psycopg.Connection,
    embedder: Embedder,
    url: str,
    ref: str,
    *,
    fetcher: Fetcher = fetch.checkout,
) -> IndexSummary:
    """Index a GitHub repo at `ref`. On any error the source is marked `failed` and the error
    re-raised; previously indexed chunks for this version stay untouched until success."""
    started = time.monotonic()
    repo = fetch.parse_github_url(url)
    fetch.validate_ref(ref)
    summary = IndexSummary(name=repo.name, version=ref)
    source_id = store.begin_source(conn, "repo", repo.name, repo.url, ref)
    try:
        documents: list[store.DocumentRecord] = []
        with fetcher(repo.url, ref) as co:  # the temp folder is gone after this block
            summary.commit_sha = co.commit_sha
            files = collect_files(co.path)
        summary.files_kept = len(files.kept)
        summary.skipped = files.skipped

        for f in files.kept:
            language, chunks = chunk_file(f.path, f.text)
            enriched = [enrich(f.path, c) for c in chunks]
            documents.append(
                store.DocumentRecord(f.path, language, len(split_lines(f.text)), enriched)
            )
            summary.chunks_by_kind.update(c.kind for c in chunks)

        all_chunks = [e for d in documents for e in d.chunks]
        embeddings = store.existing_embeddings(
            conn, "repo", repo.name, embedder.model_id, (e.content_hash for e in all_chunks)
        )
        # Embed each new hash once, even if the same chunk text appears in several files.
        todo = {
            e.content_hash: e.embedding_text for e in all_chunks if e.content_hash not in embeddings
        }
        summary.embeddings_reused = sum(1 for e in all_chunks if e.content_hash in embeddings)
        summary.embeddings_created = len(todo)
        embeddings.update(zip(todo, embedder.embed(list(todo.values())), strict=True))

        store.replace_content(
            conn, source_id, summary.commit_sha, documents, embeddings, embedder.model_id
        )
    except BaseException as exc:
        # Don't let a failure to record the failure hide the original error.
        with contextlib.suppress(Exception):
            store.mark_failed(conn, source_id, f"{type(exc).__name__}: {exc}")
        raise
    summary.seconds = time.monotonic() - started
    return summary
