"""Markdown / MDX splitter: one `section` chunk per heading (spec 02, section 3.2).

- A section runs from its heading to the line before the next heading of any level.
- Symbol = heading path, e.g. 'Tutorial > CLI Options > Required Options'.
- A heading with no body that is followed by a subheading is merged into that subsection.
- Text before the first heading becomes a section named after the file's title.
- Oversized sections are split with `size_windows`, keeping kind and symbol.

markdown-it-py does the parsing, so a `#` inside a code block is never a heading.
"""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token

from grepwise.ingest.chunkers import Chunk, make_chunk, size_windows, split_lines, trim_blank

DOCS_EXTENSIONS = (".md", ".mdx")

_FRONT_MATTER_FENCE = re.compile(r"---[ \t]*\r?\n?")
_md = MarkdownIt("commonmark")


@dataclass(frozen=True)
class Heading:
    start: int  # 1-based line of the heading
    end: int  # last line of the heading (setext headings span two lines)
    level: int
    title: str


def _is_settings(text: str) -> bool:
    """True if `text` reads as YAML settings (a mapping) or is empty."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    return data is None or isinstance(data, dict)


def front_matter_end(lines: list[str]) -> int:
    """Number of leading lines taken by YAML front matter (`---` ... `---`), or 0.

    `---` is also a Markdown horizontal rule, so the block only counts as front matter if
    what's between the fences reads as YAML settings."""
    if not lines or not _FRONT_MATTER_FENCE.fullmatch(lines[0]):
        return 0
    for i in range(1, len(lines)):
        if _FRONT_MATTER_FENCE.fullmatch(lines[i]):
            return i + 1 if _is_settings("".join(lines[1:i])) else 0
    return 0  # unterminated: treat it as ordinary text


def front_matter_title(lines: list[str], fm_end: int) -> str | None:
    if fm_end == 0:
        return None
    try:
        data = yaml.safe_load("".join(lines[1 : fm_end - 1]))
    except yaml.YAMLError:
        return None
    title = data.get("title") if isinstance(data, dict) else None
    if title is None:
        return None
    return str(title).strip() or None


def _plain_text(inline: Token) -> str:
    """Heading text without Markdown formatting: `**Bold** \\`code\\`` -> 'Bold code'."""
    parts = []
    for child in inline.children or []:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return " ".join("".join(parts).split())


def find_headings(lines: list[str], fm_end: int) -> list[Heading]:
    # Blank out front matter instead of removing it, so token line numbers stay exact.
    # (Unblanked, markdown-it would read `title: x` + `---` as a setext heading.)
    text = "\n" * fm_end + "".join(lines[fm_end:])
    tokens = _md.parse(text)
    headings = []
    for i, tok in enumerate(tokens):
        # level == 0: top-level blocks only, not headings inside lists or blockquotes.
        if tok.type == "heading_open" and tok.level == 0 and tok.map:
            start, end_exclusive = tok.map
            title = _plain_text(tokens[i + 1]) or "(untitled)"
            headings.append(Heading(start + 1, end_exclusive, int(tok.tag[1:]), title))
    return headings


def chunk_markdown(path: str, text: str) -> list[Chunk]:
    lines = split_lines(text)
    if not any(line.strip() for line in lines):
        return []
    fm_end = front_matter_end(lines)
    headings = find_headings(lines, fm_end)
    chunks: list[Chunk] = []

    def add(start: int, end: int, symbol: str) -> None:
        trimmed = trim_blank(lines, start, end)
        if trimmed:
            for s, e in size_windows(lines, *trimmed):
                chunks.append(make_chunk(lines, "section", symbol, s, e))

    # Front matter, MDX imports, intro text: everything before the first heading.
    first = headings[0].start if headings else len(lines) + 1
    title = front_matter_title(lines, fm_end) or PurePosixPath(path).name
    if any(ch.isalnum() for line in lines[: first - 1] for ch in line):  # skip a lone `---`
        add(1, first - 1, title)

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
