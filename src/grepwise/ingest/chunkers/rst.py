"""reStructuredText splitter: one `section` chunk per heading, like the Markdown splitter.

A heading is a title line underlined (and optionally overlined) with one repeated punctuation
character:

    Required options          =================
    ================          Required options
                              =================

Levels follow the order in which underline styles first appear in the file, as in docutils;
an overlined style is different from the same character underlined only. Indented lines
(literal blocks, directive bodies) are never headings, nor are underlines containing spaces
(table borders) or lone punctuation lines (transitions).
"""

import string
from pathlib import PurePosixPath

from grepwise.ingest.chunkers import Chunk, split_lines
from grepwise.ingest.chunkers.sections import Heading, assemble_sections

RST_EXTENSIONS = (".rst",)
_PUNCTUATION = frozenset(string.punctuation)


def _adornment(line: str) -> str | None:
    """The character of an underline/overline ('=====' -> '='), or None."""
    s = line.rstrip()
    if len(s) < 2 or s[0] not in _PUNCTUATION or set(s) != {s[0]}:
        return None
    return s[0]


def _is_title(line: str, *, inset_ok: bool = False) -> bool:
    s = line.rstrip()
    if not s.strip() or _adornment(line) is not None:
        return False
    return inset_ok or not s[0].isspace()


def _clean(title: str) -> str:
    return " ".join(title.replace("``", "").split()) or "(untitled)"


def find_rst_headings(lines: list[str]) -> list[Heading]:
    styles: list[tuple[str, bool]] = []  # (character, overlined), in order of first use
    headings: list[Heading] = []

    def level(style: tuple[str, bool]) -> int:
        if style not in styles:
            styles.append(style)
        return styles.index(style) + 1

    i, n = 0, len(lines)
    while i < n:
        after_blank = i == 0 or not lines[i - 1].strip()
        over = _adornment(lines[i])
        if (
            over is not None
            and after_blank
            and i + 2 < n
            and _is_title(lines[i + 1], inset_ok=True)
            and _adornment(lines[i + 2]) == over
            and len(lines[i].rstrip()) >= len(lines[i + 1].strip())
        ):
            title = lines[i + 1]
            headings.append(Heading(i + 1, i + 3, level((over, True)), _clean(title)))
            i += 3
            continue
        if after_blank and i + 1 < n and _is_title(lines[i]):
            under = _adornment(lines[i + 1])
            if under is not None and len(lines[i + 1].rstrip()) >= len(lines[i].rstrip()):
                headings.append(Heading(i + 1, i + 2, level((under, False)), _clean(lines[i])))
                i += 2
                continue
        i += 1
    return headings


def chunk_rst(path: str, text: str) -> list[Chunk]:
    lines = split_lines(text)
    if not any(line.strip() for line in lines):
        return []
    headings = find_rst_headings(lines)
    # Labels and directives before the first heading (`.. _quickstart:`, `:orphan:`) are
    # markup, not content: don't make an intro section out of them.
    first = headings[0].start if headings else len(lines) + 1
    intro = [line for line in lines[: first - 1] if line.strip()]
    if intro and all(line.lstrip().startswith(("..", ":")) for line in intro):
        lines = ["\n"] * (first - 1) + lines[first - 1 :]
    return assemble_sections(lines, headings, PurePosixPath(path).name)
