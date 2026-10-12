"""Hybrid search: vector + keyword, merged with reciprocal rank fusion (spec 02, section 6;
SQLite since v2, spec 11).

All user text reaches SQL as bound parameters; the SQL strings themselves are constants.
"""

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from grepwise.db.connection import Conn, json_list, transaction
from grepwise.ingest.embed import Embedder
from grepwise.ingest.keywords import query_terms
from grepwise.search.citations import format_citation, split_ref
from grepwise.search.links import link_for
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
    symbol_rank: int | None = None  # 1-based position among exact symbol-name matches
    link: str | None = None  # GitHub URL or local path to the cited lines


@dataclass(frozen=True)
class Ranked:
    chunk_id: int
    score: float
    vector_rank: int | None
    keyword_rank: int | None
    symbol_rank: int | None = None


def rrf_merge(
    vector_ids: Sequence[int],
    keyword_ids: Sequence[int],
    symbol_ids: Sequence[int] = (),
    k: int = RRF_K,
) -> list[Ranked]:
    """score = sum of 1 / (k + rank) over the lists an item appears in (ranks start at 1).
    Ties break on the best single rank, then on chunk id, so the order is deterministic."""
    lists = [vector_ids, keyword_ids, symbol_ids]
    ranks = [{cid: r for r, cid in enumerate(ids, 1)} for ids in lists]
    merged = []
    for cid in dict.fromkeys([*vector_ids, *keyword_ids, *symbol_ids]):
        v, kw, sym = (r.get(cid) for r in ranks)
        score = sum(1 / (k + r) for r in (v, kw, sym) if r is not None)
        merged.append(Ranked(cid, score, v, kw, sym))
    worst = sum(len(ids) for ids in lists) + 1
    return sorted(
        merged,
        key=lambda r: (
            -r.score,
            min(r.vector_rank or worst, r.keyword_rank or worst, r.symbol_rank or worst),
            r.chunk_id,
        ),
    )


def parse_sources(sources: Sequence[str] | None) -> list[tuple[str, str]] | None:
    """['pallets/itsdangerous', 'petstore@1.0.27'] -> [(name, version)]; '' = any version."""
    if not sources:
        return None
    return [split_ref(s) for s in sources]


def matching_sources(
    conn: Conn, sources: Sequence[str] | None, source_ids: Sequence[int] | None = None
) -> list[tuple[int, int]]:
    """(id, generation) of every source matching 'name' or 'name@version' (all if None),
    or, with `source_ids`, of exactly those sources."""
    wanted = parse_sources(sources)
    ids = set(source_ids) if source_ids is not None else None
    rows = conn.execute("SELECT id, name, version, generation FROM sources ORDER BY id")
    return [
        (sid, gen)
        for sid, name, version, gen in rows
        if (ids is None or sid in ids)
        and (wanted is None or any(n == name and v in ("", version) for n, v in wanted))
    ]


# Source and kind filters, as JSON lists (NULL = no filter). Used by search and find_symbol.
FILTERS_SQL = """
  AND (:source_ids IS NULL OR c.source_id IN (SELECT value FROM json_each(:source_ids)))
  AND (:kinds IS NULL OR c.kind IN (SELECT value FROM json_each(:kinds)))
"""


def filter_params(
    conn: Conn,
    sources: Sequence[str] | None,
    kinds: Sequence[str] | None,
    source_ids: Sequence[int] | None = None,
) -> dict[str, Any]:
    narrowed = sources or source_ids is not None
    ids = [sid for sid, _ in matching_sources(conn, sources, source_ids)] if narrowed else None
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
       c.start_line, c.end_line, c.content, s.url, s.ref
FROM chunks c
JOIN documents d ON d.id = c.document_id
JOIN sources s ON s.id = c.source_id
WHERE c.id IN (SELECT value FROM json_each(?))
"""


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_CODE_SHAPED = re.compile(r"_|\.|[a-z][A-Z]|[A-Z]{2,}[a-z]")  # snake_case, a.b, camelCase


def symbol_terms(query: str) -> list[str]:
    """Words in a question that look like code names: 'How does TimestampSigner use max_age?'
    -> ['TimestampSigner', 'max_age']. A one-word query counts as a name. For 'typer.Option'
    the last part ('Option') is tried too, since symbols are stored by their own name."""
    words = _IDENTIFIER.findall(query)
    one_word = len(query.split()) == 1
    named = words if one_word else [w for w in words if _CODE_SHAPED.search(w)]
    out = [*named, *(w.rsplit(".", 1)[-1] for w in named if "." in w)]
    return list(dict.fromkeys(out))[:10]


# Definitions first (a class or function beats a module or window that mentions the name).
_SYMBOL_SQL = f"""
SELECT c.id
FROM chunks c
WHERE (c.symbol IN (SELECT value FROM json_each(:names))
       OR c.symbol_short IN (SELECT value FROM json_each(:names))) {FILTERS_SQL}
ORDER BY CASE c.kind WHEN 'class' THEN 0 WHEN 'function' THEN 0 WHEN 'method' THEN 0
                     WHEN 'section' THEN 1 ELSE 2 END, c.id
LIMIT :n
"""


def symbol_search(conn: Conn, query: str, filters: dict[str, Any], n: int) -> list[int]:
    names = symbol_terms(query)
    if not names:
        return []
    params = {"names": json_list(names), "n": n, **filters}
    return [row[0] for row in conn.execute(_SYMBOL_SQL, params)]


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
    source_ids: Sequence[int] | None = None,  # exactly these sources (the MCP tools use ids)
) -> list[SearchResult]:
    clock = time.perf_counter
    started = clock()
    q = embedder.embed_query(query) if mode != "keyword" else None  # before the transaction
    embedded = clock()
    # One read transaction = one snapshot for every query below, so a re-index that swaps this
    # source's chunks mid-search can't make the details lookup miss ids.
    with transaction(conn):
        matched = matching_sources(conn, sources, source_ids)
        filters = filter_params(conn, sources, kinds, source_ids)
        vec: list[int] = []
        if q is not None:
            vec = vector_search(conn, q, embedder.model_id, matched, kinds, candidates)
        vectored = clock()
        kw = keyword_search(conn, query, filters, candidates) if mode != "vector" else []
        sym = symbol_search(conn, query, filters, candidates) if mode == "hybrid" else []
        keyworded = clock()
        merged = rrf_merge(vec, kw, sym)
        ids = [r.chunk_id for r in merged]
        rows = {row[0]: row for row in conn.execute(_DETAILS_SQL, (json_list(ids),))}
    if timings is not None:
        timings.update(
            embed=(embedded - started) * 1000,
            vector=(vectored - embedded) * 1000,
            keyword=(keyworded - vectored) * 1000,
        )

    results: list[SearchResult] = []
    seen: set[str] = set()
    tokens = 0
    for r in merged:
        if len(results) >= limit:
            break
        if r.chunk_id not in rows:  # can't happen within one snapshot; never crash on it
            continue
        _, name, version, type_, path, kind, symbol, start, end, content, url, ref = rows[
            r.chunk_id
        ]
        citation = format_citation(name, version, path, start, end)
        if citation in seen:
            continue  # the same lines from a second source (e.g. a repo and its docs folder)
        seen.add(citation)
        cost = len(content) // 4
        if token_budget is not None and results and tokens + cost > token_budget:
            break  # always return at least one result
        tokens += cost
        results.append(
            SearchResult(
                citation=citation,
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
                symbol_rank=r.symbol_rank,
                link=link_for(url, ref, version, path, start, end, type_),
            )
        )
    return results
