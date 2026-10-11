"""Markdown / MDX splitter: one `section` chunk per heading (spec 02, section 3.2; the
section rules are in sections.py).

markdown-it-py does the parsing, so a `#` inside a code block is never a heading.
"""

import re
from pathlib import PurePosixPath

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token

from grepwise.ingest.chunkers import Chunk, split_lines
from grepwise.ingest.chunkers.sections import Heading, assemble_sections

DOCS_EXTENSIONS = (".md", ".mdx")

_FRONT_MATTER_FENCE = re.compile(r"---[ \t]*\r?\n?")
_md = MarkdownIt("commonmark")


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
    title = front_matter_title(lines, fm_end) or PurePosixPath(path).name
    return assemble_sections(lines, headings, title)
