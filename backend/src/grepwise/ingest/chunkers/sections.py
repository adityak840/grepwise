"""Turning a document's headings into `section` chunks, for every docs format.

- A section runs from its heading to the line before the next heading of any level.
- Symbol = heading path, e.g. 'Tutorial > CLI Options > Required Options'.
- A heading with no body that is followed by a subheading is merged into that subsection.
- Text before the first heading becomes a section named `intro_title` (the file's title).
- Oversized sections are split with `size_windows`, keeping kind and symbol.
"""

from dataclasses import dataclass

from grepwise.ingest.chunkers import Chunk, make_chunk, size_windows, trim_blank


@dataclass(frozen=True)
class Heading:
    start: int  # 1-based first line of the heading
    end: int  # last line of the heading (setext/underlined headings span 2-3 lines)
    level: int
    title: str


def assemble_sections(lines: list[str], headings: list[Heading], intro_title: str) -> list[Chunk]:
    chunks: list[Chunk] = []

    def add(start: int, end: int, symbol: str) -> None:
        trimmed = trim_blank(lines, start, end)
        if trimmed:
            for s, e in size_windows(lines, *trimmed):
                chunks.append(make_chunk(lines, "section", symbol, s, e))

    # Front matter, imports, intro text: everything before the first heading.
    first = headings[0].start if headings else len(lines) + 1
    if any(ch.isalnum() for line in lines[: first - 1] for ch in line):  # skip a lone `---`
        add(1, first - 1, intro_title)

    stack: list[Heading] = []  # the current heading path
    pending_start: int | None = None  # start of body-less parent headings being merged
    for i, h in enumerate(headings):
        while stack and stack[-1].level >= h.level:
            stack.pop()
        stack.append(h)
        nxt = headings[i + 1] if i + 1 < len(headings) else None
        end = nxt.start - 1 if nxt else len(lines)
        start = pending_start or h.start
        body_empty = trim_blank(lines, h.end + 1, end) is None if h.end < end else True
        if body_empty and nxt is not None and nxt.level > h.level:
            pending_start = start  # merge into the subsection that follows
            continue
        pending_start = None
        add(start, end, " > ".join(s.title for s in stack))
    return chunks
