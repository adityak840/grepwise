"""Checking an answer's citations: each must exist (verify_citation) AND lie within ranges that
tools actually returned in this run. A real citation the model never saw is dropped as
`unseen`, because it can't have come from the sources."""

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Literal

import psycopg

from grepwise.search.citations import CitationError, parse_citation
from grepwise.tools.verify import VerifiedCitation, verify_citation

Status = Literal["verified", "not_found", "bad_range", "unseen"]

# Well-formed: [[name@version:path:Lstart-Lend]]. Anchored on the line range, so paths may
# contain brackets (Next.js `app/[slug]/page.tsx`); can't start inside another `[[`.
MARKER = re.compile(r"\[\[((?:(?!\[\[)[^\n])+?:L\d+-L\d+)\s*\]\]")
# Anything else in [[...]] that looks like an attempted citation (e.g. `[[README.md lines 1-3]]`)
# is reported as not_found. Code like `x[[0]]` or `df[['col']]` is not.
_OTHER_MARKER = re.compile(r"\[\[([^\[\]\n]+?)\]\]")


def _looks_like_citation(text: str) -> bool:
    return any(ch in text for ch in "@/.") and not any(q in text for q in "'\"")


@dataclass(frozen=True)
class CitationCheck:
    citation: str
    status: Status
    content: str | None = None  # the verified lines, for `verified` only

    def to_event(self) -> dict[str, Any]:
        data: dict[str, Any] = {"citation": self.citation, "status": self.status}
        if self.content is not None:
            data["content"] = self.content
        return data


class SeenRanges:
    """Every (source, path, line range) that a tool showed the model in this run."""

    def __init__(self) -> None:
        self._ranges: dict[tuple[str, str, str], list[tuple[int, int]]] = defaultdict(list)

    def add(self, citation: str) -> None:
        try:
            c = parse_citation(citation)
        except CitationError:
            return
        self._ranges[(c.name, c.version, c.path)].append((c.start_line, c.end_line))

    def add_result(self, tool: str, result: dict[str, Any]) -> None:
        """Record what a call_tool result showed."""
        items: list[Any] = {
            "search": result.get("results"),
            "find_symbol": result.get("matches"),
            "get_endpoint": result.get("endpoints"),
            "read_file": [result] if "citation" in result else [],
        }.get(tool) or []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("citation"), str):
                self.add(item["citation"])

    def __bool__(self) -> bool:
        """True once any tool has shown the model something citable."""
        return bool(self._ranges)

    def covers(self, name: str, version: str, path: str, start: int, end: int) -> bool:
        """True if lines start..end all lie within what tools showed (results that overlap or
        touch are merged first). A citation wider than what was shown doesn't count."""
        merged: list[list[int]] = []
        for s, e in sorted(self._ranges.get((name, version, path), [])):
            if merged and s <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        return any(s <= start and end <= e for s, e in merged)


def extract(answer: str) -> list[str]:
    """Citation markers in order of first appearance, without duplicates: well-formed ones, plus
    malformed attempts (so they're reported rather than silently ignored)."""
    found = [(m.start(), m.group(1).strip()) for m in MARKER.finditer(answer)]
    rest = MARKER.sub(lambda m: " " * len(m.group(0)), answer)  # keep positions
    found += [
        (m.start(), m.group(1).strip())
        for m in _OTHER_MARKER.finditer(rest)
        if _looks_like_citation(m.group(1))
    ]
    return list(dict.fromkeys(text for _, text in sorted(found) if text))


def check_citations(conn: psycopg.Connection, answer: str, seen: SeenRanges) -> list[CitationCheck]:
    checks = []
    for citation in extract(answer):
        result = verify_citation(conn, citation)
        if not isinstance(result, VerifiedCitation):
            status: Status = "bad_range" if result.error.code == "bad_line_range" else "not_found"
            checks.append(CitationCheck(citation, status))
            continue
        c = parse_citation(citation)
        if not seen.covers(c.name, c.version, c.path, c.start_line, c.end_line):
            checks.append(CitationCheck(citation, "unseen"))
            continue
        checks.append(CitationCheck(citation, "verified", result.content))
    return checks
