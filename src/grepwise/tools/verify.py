"""Checking a citation against the database: the source, path and lines must exist."""

from pydantic import BaseModel

from grepwise.db.connection import Conn
from grepwise.search.citations import CitationError, parse_citation
from grepwise.tools.errors import ToolError, ToolFailure, returns_tool_errors
from grepwise.tools.read_file import load_document


class VerifiedCitation(BaseModel):
    citation: str
    source: str
    path: str
    start_line: int
    end_line: int
    content: str  # exactly the cited lines


@returns_tool_errors
def verify_citation(conn: Conn, citation: str) -> VerifiedCitation:
    try:
        c = parse_citation(citation)
    except CitationError:
        raise ToolFailure(
            "bad_input",
            f"Not a citation: {citation!r}.",
            "Use the form name@version:path:L<start>-L<end>, copied from a tool result.",
        ) from None
    known = conn.execute(
        "SELECT 1 FROM sources WHERE name = ? AND version = ? LIMIT 1", (c.name, c.version)
    ).fetchone()
    if known is None:
        raise ToolFailure(
            "source_not_found",
            f"{c.source} is not indexed.",
            "Only cite sources returned by list_sources, at their exact version.",
        )
    lines = load_document(conn, c.name, c.version, c.path)
    if c.end_line > len(lines):
        raise ToolFailure(
            "bad_line_range",
            f"{c.path} has {len(lines)} lines; the citation ends at line {c.end_line}.",
            "Cite only lines that a tool returned.",
        )
    return VerifiedCitation(
        citation=str(c),
        source=c.source,
        path=c.path,
        start_line=c.start_line,
        end_line=c.end_line,
        content="".join(lines[c.start_line - 1 : c.end_line]),
    )


__all__ = ["ToolError", "VerifiedCitation", "verify_citation"]
