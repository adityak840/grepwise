"""Splitting files into chunks with exact, 1-based, inclusive line ranges.

Every chunk's `content` is exactly `"".join(lines[start_line - 1 : end_line])`,
where `lines = split_lines(file_text)`. All chunkers build chunks via
`make_chunk`, so this holds by construction.
"""

from dataclasses import dataclass

# ~1,500 tokens, estimating tokens as characters / 4.
MAX_CHUNK_TOKENS = 1_500
MAX_CHUNK_CHARS = MAX_CHUNK_TOKENS * 4
WINDOW_OVERLAP_RATIO = 0.15

# Files with a longer line are skipped by the filter (they're almost always data, minified or
# generated). Since every indexed line fits in a chunk, no chunk ever exceeds MAX_CHUNK_CHARS.
MAX_LINE_CHARS = 2_000
assert MAX_LINE_CHARS <= MAX_CHUNK_CHARS


@dataclass(frozen=True)
class Chunk:
    kind: str  # function | method | class | module | window | section | endpoint
    symbol: str | None  # qualified, e.g. 'Signer.sign'
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    content: str
    parent: str | None = None  # e.g. 'class Signer', for the context header


def split_lines(text: str) -> list[str]:
    """Split on '\\n' only, keeping line endings.

    Unlike str.splitlines(), this ignores \\r, \\x0c, \\u2028 and friends, so the
    numbering matches git, GitHub and tree-sitter.
    """
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def make_chunk(
    lines: list[str],
    kind: str,
    symbol: str | None,
    start: int,
    end: int,
    parent: str | None = None,
) -> Chunk:
    if not 1 <= start <= end <= len(lines):
        raise ValueError(f"bad line range {start}-{end} for {len(lines)} lines")
    return Chunk(kind, symbol, start, end, "".join(lines[start - 1 : end]), parent)


def range_chars(lines: list[str], start: int, end: int) -> int:
    return sum(len(line) for line in lines[start - 1 : end])


def trim_blank(lines: list[str], start: int, end: int) -> tuple[int, int] | None:
    """Shrink a range to drop blank lines at both ends; None if nothing is left."""
    while start <= end and not lines[start - 1].strip():
        start += 1
    while end >= start and not lines[end - 1].strip():
        end -= 1
    return (start, end) if start <= end else None


def size_windows(
    lines: list[str],
    start: int,
    end: int,
    max_chars: int = MAX_CHUNK_CHARS,
    overlap_ratio: float = WINDOW_OVERLAP_RATIO,
) -> list[tuple[int, int]]:
    """Cover start..end with windows of at most `max_chars` (a single longer line
    gets a window of its own). Each window overlaps the previous one by
    ~`overlap_ratio` of the previous window's lines."""
    windows: list[tuple[int, int]] = []
    s = start
    while True:
        e, size = s, len(lines[s - 1])
        while e < end and size + len(lines[e]) <= max_chars:
            size += len(lines[e])  # lines[e] is line e + 1
            e += 1
        windows.append((s, e))
        if e >= end:
            return windows
        n = e - s + 1
        overlap = min(round(n * overlap_ratio), n - 1)
        s = e + 1 - overlap
