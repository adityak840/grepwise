"""Hybrid search: vector + keyword, merged with reciprocal rank fusion (spec 02, section 6;
SQLite since v2, spec 11).

All user text reaches SQL as bound parameters; the SQL strings themselves are constants.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from grepwise.db.connection import Conn, json_list, transaction
from grepwise.ingest.embed import Embedder
from grepwise.ingest.keywords import query_terms
from grepwise.search.citations import format_citation
from grepwise.search.vectors import vector_search

RRF_K = 60
DEFAULT_CANDIDATES = 50
DEFAULT_TOKEN_BUDGET = 4_000

Mode = Literal["hybrid", "vector", "keyword"]


@dataclass
class SearchResult:
    citation: str  # 'fastapi/typer@0.27.2:docs/tutorial/options/required.md:L1-L24'
    source: str  # 'fastapi/typer@0.27.2'
    source_type: str  # repo | docs | openapi
    path: str
    kind: str
    symbol: str | None
    start_line: int
    end_line: int
    content: str
    score: float  # RRF score
    vector_rank: int | None  # 1-based position in the vector list, if it appeared there
    keyword_rank: int | None  # 1-based position in the keyword list, if it appeared there


@dataclass(frozen=True)
class Ranked:
    chunk_id: int
    score: float
    vector_rank: int | None
    keyword_rank: int | None


def rrf_merge(
    vector_ids: Sequence[int], keyword_ids: Sequence[int], k: int = RRF_K
) -> list[Ranked]:
    """score = sum of 1 / (k + rank) over the lists an item appears in (ranks start at 1).
    Ties break on the best single rank, then on chunk id, so the order is deterministic."""
    vranks = {cid: r for r, cid in enumerate(vector_ids, 1)}
    kranks = {cid: r for r, cid in enumerate(keyword_ids, 1)}
    merged = []
    for cid in dict.fromkeys([*vector_ids, *keyword_ids]):
        v, kw = vranks.get(cid), kranks.get(cid)
        score = sum(1 / (k + r) for r in (v, kw) if r is not None)
        merged.append(Ranked(cid, score, v, kw))
    best = len(vector_ids) + len(keyword_ids) + 1
    return sorted(
        merged,
        key=lambda r: (-r.score, min(r.vector_rank or best, r.keyword_rank or best), r.chunk_id),
    )


def parse_sources(sources: Sequence[str] | None) -> list[tuple[str, str]] | None:
    """['pallets/itsdangerous', 'petstore@1.0.27'] -> [(name, version)]; '' = any version."""
    if not sources:
        return None
    return [(name, version) for name, _, version in (s.partition("@") for s in sources)]


def matching_sources(conn: Conn, sources: Sequence[str] | None) -> list[tuple[int, int]]:
    """(id, generation) of every source matching 'name' or 'name@version' (all if None)."""
    wanted = parse_sources(sources)
    rows = conn.execute("SELECT id, name, version, generation FROM sources ORDER BY id")
    return [
        (sid, gen)
        for sid, name, version, gen in rows
        if wanted is None or any(n == name and v in ("", version) for n, v in wanted)
    ]


# Source and kind filters, as JSON lists (NULL = no filter). Used by search and find_symbol.
FILTERS_SQL = """
  AND (:source_ids IS NULL OR c.source_id IN (SELECT value FROM json_each(:source_ids)))
  AND (:kinds IS NULL OR c.kind IN (SELECT value FROM json_each(:kinds)))
"""


def filter_params(
    conn: Conn, sources: Sequence[str] | None, kinds: Sequence[str] | None
) -> dict[str, Any]:
    ids = [sid for sid, _ in matching_sources(conn, sources)] if sources else None
    return {"source_ids": json_list(ids), "kinds": json_list(kinds) if kinds else None}


_KEYWORD_SQL = f"""
SELECT chunks_fts.rowid
FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid
WHERE chunks_fts MATCH :match {FILTERS_SQL}
ORDER BY chunks_fts.rank, c.id
LIMIT :n
"""  # rank = BM25: words that are rare across all chunks count for more

_DETAILS_SQL = """
SELECT c.id, s.name, s.version, s.type, d.path, c.kind, c.symbol,
       c.start_line, c.end_line, c.content
FROM chunks c
JOIN documents d ON d.id = c.document_id
JOIN sources s ON s.id = c.source_id
WHERE c.id IN (SELECT value FROM json_each(?))
"""


def fts_query(query: str) -> str | None:
    """'How do I sign?' -> '"how" OR "do" OR "i" OR "sign"'. Every term is quoted, and terms
    are letters and digits only (see query_terms), so nothing can be read as FTS5 syntax."""
    terms = query_terms(query)
    return " OR ".join(f'"{t}"' for t in terms) if terms else None


def keyword_search(conn: Conn, query: str, filters: dict[str, Any], n: int) -> list[int]:
    match = fts_query(query)
    if match is None:
        return []
    params = {"match": match, "n": n, **filters}
    return [row[0] for row in conn.execute(_KEYWORD_SQL, params)]


def search(
    conn: Conn,
    embedder: Embedder,
    query: str,
    *,
    sources: list[str] | None = None,  # 'name' or 'name@version'
    kinds: list[str] | None = None,  # e.g. ['function', 'section']
    limit: int = 10,
    token_budget: int | None = DEFAULT_TOKEN_BUDGET,  # None: no budget, just `limit`
    candidates: int = DEFAULT_CANDIDATES,
    mode: Mode = "hybrid",
    timings: dict[str, float] | None = None,  # filled with milliseconds per step, if given
) -> list[SearchResult]:
    clock = time.perf_counter
    started = clock()
    q = embedder.embed_query(query) if mode != "keyword" else None  # before the transaction
    embedded = clock()
    # One read transaction = one snapshot for every query below, so a re-index that swaps this
    # source's chunks mid-search can't make the details lookup miss ids.
    with transaction(conn):
        matched = matching_sources(conn, sources)
        filters = filter_params(conn, sources, kinds)
        vec: list[int] = []
        if q is not None:
            vec = vector_search(conn, q, embedder.model_id, matched, kinds, candidates)
        vectored = clock()
        kw = keyword_search(conn, query, filters, candidates) if mode != "vector" else []
        keyworded = clock()
        merged = rrf_merge(vec, kw)
        ids = [r.chunk_id for r in merged]
        rows = {row[0]: row for row in conn.execute(_DETAILS_SQL, (json_list(ids),))}
    if timings is not None:
        timings.update(
            embed=(embedded - started) * 1000,
            vector=(vectored - embedded) * 1000,
            keyword=(keyworded - vectored) * 1000,
        )

    results: list[SearchResult] = []
    tokens = 0
    for r in merged:
        if len(results) >= limit:
            break
        if r.chunk_id not in rows:  # can't happen within one snapshot; never crash on it
            continue
        _, name, version, type_, path, kind, symbol, start, end, content = rows[r.chunk_id]
        cost = len(content) // 4
        if token_budget is not None and results and tokens + cost > token_budget:
            break  # always return at least one result
        tokens += cost
        results.append(
            SearchResult(
                citation=format_citation(name, version, path, start, end),
                source=f"{name}@{version}",
                source_type=type_,
                path=path,
                kind=kind,
                symbol=symbol,
                start_line=start,
                end_line=end,
                content=content,
                score=r.score,
                vector_rank=r.vector_rank,
                keyword_rank=r.keyword_rank,
            )
        )
    return results
