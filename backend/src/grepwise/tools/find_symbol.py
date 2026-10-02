"""The find_symbol tool: exact lookup of a function, class, method or docs section by name."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from grepwise.search.citations import format_citation
from grepwise.search.hybrid import FILTERS_SQL, filter_params
from grepwise.tools.context import ToolContext
from grepwise.tools.errors import returns_tool_errors
from grepwise.tools.sources import check_refs

MAX_MATCHES = 20
_MAX_ROWS = 200  # chunk rows fetched before merging windows into matches

# Both branches use indexes: (symbol), (symbol_short), (lower(symbol)), (lower(symbol_short)).
_EXACT_SQL = f"""
SELECT s.name, s.version, d.path, c.kind, c.symbol, c.start_line, c.end_line, c.content,
       c.symbol = %(name)s AS qualified
FROM chunks c JOIN documents d ON d.id = c.document_id JOIN sources s ON s.id = c.source_id
WHERE (c.symbol = %(name)s OR c.symbol_short = %(name)s) {FILTERS_SQL}
ORDER BY qualified DESC, s.name, s.version, d.path, c.start_line
LIMIT %(n)s
"""

_CASELESS_SQL = f"""
SELECT s.name, s.version, d.path, c.kind, c.symbol, c.start_line, c.end_line, c.content,
       lower(c.symbol) = lower(%(name)s) AS qualified
FROM chunks c JOIN documents d ON d.id = c.document_id JOIN sources s ON s.id = c.source_id
WHERE (lower(c.symbol) = lower(%(name)s) OR lower(c.symbol_short) = lower(%(name)s))
  {FILTERS_SQL}
ORDER BY qualified DESC, s.name, s.version, d.path, c.start_line
LIMIT %(n)s
"""

_COMMENT_PREFIXES = ("#", "//", "/*", "*", '"""', "'''")


def signature(kind: str, content: str) -> str:
    """The first meaningful line: skips blanks, comments and decorators in code; for docs and
    endpoints, the first non-blank line (a heading or 'GET /path')."""
    for line in content.splitlines():
        text = line.strip()
        if not text:
            continue
        if kind in ("section", "endpoint"):
            return text[:200]
        if text.startswith(_COMMENT_PREFIXES) or text.startswith("@"):
            continue
        return text[:200]
    return ""


class FindSymbolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(
        min_length=1,
        description="Exact name: qualified ('Signer.sign'), short ('sign'), or a docs heading "
        "('Required CLI Options'). Case-sensitive first, then case-insensitive.",
    )
    sources: list[str] | None = Field(
        default=None, description="Only these sources: 'name' (all versions) or 'name@version'."
    )
    kinds: list[str] | None = Field(
        default=None, description="Only these kinds: function, method, class, section, ..."
    )


class SymbolMatch(BaseModel):
    citation: str
    symbol: str
    kind: str
    source: str
    path: str
    signature: str = Field(description="First line of the definition")


class FindSymbolOutput(BaseModel):
    matches: list[SymbolMatch]
    case_insensitive: bool = Field(description="True if only case-insensitive matches existed")
    truncated: bool


def _merge(rows: list[Any]) -> list[SymbolMatch]:
    """One match per symbol per file and kind: the windows of an oversized function (which
    overlap or touch) become one range. Rows arrive sorted by start line within a file."""
    spans: list[list[Any]] = []  # [name, version, path, symbol, kind, content, start, end]
    last: dict[tuple[str, str, str, str, str], list[Any]] = {}
    for name, version, path, kind, symbol, start, end, content, _ in rows:
        key = (name, version, path, symbol, kind)
        prev = last.get(key)
        if prev is not None and start <= prev[7] + 1:
            prev[7] = max(prev[7], end)
            continue
        span = [name, version, path, symbol, kind, content, start, end]
        spans.append(span)
        last[key] = span
    return [
        SymbolMatch(
            citation=format_citation(name, version, path, start, end),
            symbol=symbol,
            kind=kind,
            source=f"{name}@{version}",
            path=path,
            signature=signature(kind, content),
        )
        for name, version, path, symbol, kind, content, start, end in spans
    ]


@returns_tool_errors
def find_symbol(ctx: ToolContext, inp: FindSymbolInput) -> FindSymbolOutput:
    check_refs(ctx, inp.sources)
    params = {"name": inp.name.strip(), "n": _MAX_ROWS, **filter_params(inp.sources, inp.kinds)}
    rows = ctx.conn.execute(_EXACT_SQL, params).fetchall()
    caseless = not rows
    if caseless:
        rows = ctx.conn.execute(_CASELESS_SQL, params).fetchall()
    matches = _merge(rows)
    return FindSymbolOutput(
        matches=matches[:MAX_MATCHES],
        case_insensitive=caseless and bool(matches),
        truncated=len(matches) > MAX_MATCHES or len(rows) == _MAX_ROWS,
    )
