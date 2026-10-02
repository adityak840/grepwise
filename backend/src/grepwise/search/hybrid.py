"""Hybrid search: vector + keyword, merged with reciprocal rank fusion (spec 02, section 6).

All user text reaches SQL as bound parameters; the SQL strings themselves are constants.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import psycopg
from pgvector import HalfVector

from grepwise.ingest.embed import Embedder
from grepwise.ingest.keywords import query_terms
from grepwise.search.citations import format_citation

RRF_K = 60
DEFAULT_CANDIDATES = 50
DEFAULT_TOKEN_BUDGET = 4_000


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


def parse_sources(sources: Sequence[str] | None) -> tuple[list[str], list[str]] | None:
    """['pallets/itsdangerous', 'petstore@1.0.27'] -> (names, versions); '' = any version."""
    if not sources:
        return None
    names, versions = [], []
    for s in sources:
        name, _, version = s.partition("@")
        names.append(name)
        versions.append(version)
    return names, versions


FILTERS_SQL = """
  AND (%(f_names)s::text[] IS NULL OR EXISTS (
        SELECT 1 FROM unnest(%(f_names)s::text[], %(f_versions)s::text[]) AS f(name, version)
        WHERE f.name = s.name AND (f.version = '' OR f.version = s.version)))
  AND (%(f_kinds)s::text[] IS NULL OR c.kind = ANY(%(f_kinds)s::text[]))
"""

_VECTOR_SQL = f"""
WITH ranked AS MATERIALIZED (
  SELECT c.id, c.embedding <=> %(q)s AS distance
  FROM chunks c JOIN sources s ON s.id = c.source_id
  WHERE c.embedding_model = %(model)s {FILTERS_SQL}
  ORDER BY c.embedding <=> %(q)s
  LIMIT %(n)s
)
SELECT id FROM ranked ORDER BY distance, id
"""  # relaxed_order can return near-ordered rows, hence the re-sort

_KEYWORD_SQL = f"""
WITH q AS (SELECT to_tsquery('english', %(tsq)s) || to_tsquery('simple', %(tsq)s) AS query)
SELECT c.id
FROM chunks c JOIN sources s ON s.id = c.source_id, q
WHERE c.tsv @@ q.query {FILTERS_SQL}
ORDER BY ts_rank_cd(c.tsv, q.query) DESC, c.id
LIMIT %(n)s
"""

_DETAILS_SQL = """
SELECT c.id, s.name, s.version, s.type, d.path, c.kind, c.symbol,
       c.start_line, c.end_line, c.content
FROM chunks c
JOIN documents d ON d.id = c.document_id
JOIN sources s ON s.id = c.source_id
WHERE c.id = ANY(%s)
"""


def filter_params(sources: Sequence[str] | None, kinds: Sequence[str] | None) -> dict[str, Any]:
    parsed = parse_sources(sources)
    return {
        "f_names": parsed[0] if parsed else None,
        "f_versions": parsed[1] if parsed else None,
        "f_kinds": list(kinds) if kinds else None,
    }


def vector_search(
    conn: psycopg.Connection, q: HalfVector, model_id: str, filters: dict[str, Any], n: int
) -> list[int]:
    """Call inside a transaction: the SET LOCALs last until it ends."""
    conn.execute("SET LOCAL hnsw.ef_search = 100")
    # pgvector 0.8+: keep scanning the index until enough rows pass the filters.
    conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
    params = {"q": q, "model": model_id, "n": n, **filters}
    return [row[0] for row in conn.execute(_VECTOR_SQL, params)]


def keyword_search(
    conn: psycopg.Connection, query: str, filters: dict[str, Any], n: int
) -> list[int]:
    terms = query_terms(query)
    if not terms:
        return []
    # OR between words: a natural question's words won't all be in one chunk. Terms are
    # letters and digits only (see query_terms), and still passed as a parameter.
    params = {"tsq": " | ".join(terms), "n": n, **filters}
    return [row[0] for row in conn.execute(_KEYWORD_SQL, params)]


def search(
    conn: psycopg.Connection,
    embedder: Embedder,
    query: str,
    *,
    sources: list[str] | None = None,  # 'name' or 'name@version'
    kinds: list[str] | None = None,  # e.g. ['function', 'section']
    limit: int = 10,
    token_budget: int | None = DEFAULT_TOKEN_BUDGET,  # None: no budget, just `limit`
    candidates: int = DEFAULT_CANDIDATES,
) -> list[SearchResult]:
    filters = filter_params(sources, kinds)
    q = HalfVector(embedder.embed_query(query))  # network call: before the transaction opens
    # One REPEATABLE READ transaction = one snapshot for all three queries, so a re-index
    # that swaps this source's chunks mid-search can't make the details lookup miss ids.
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        merged = rrf_merge(
            vector_search(conn, q, embedder.model_id, filters, candidates),
            keyword_search(conn, query, filters, candidates),
        )
        ids = [r.chunk_id for r in merged]
        rows = {row[0]: row for row in conn.execute(_DETAILS_SQL, (ids,))} if ids else {}

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
