"""Line-window splitter for unsupported languages and files that fail to parse."""

from grepwise.ingest.chunkers import MAX_CHUNK_CHARS, Chunk, make_chunk, split_lines

WINDOW_LINES = 80
OVERLAP_LINES = 15


def chunk_fallback(
    text: str,
    window: int = WINDOW_LINES,
    overlap: int = OVERLAP_LINES,
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[Chunk]:
    """Windows of up to `window` lines and `max_chars` characters (a single longer line gets a
    window of its own). Full-size windows overlap by `overlap` lines; shorter ones overlap
    proportionally less."""
    lines = split_lines(text)
    if not any(line.strip() for line in lines):
        return []
    chunks: list[Chunk] = []
    start = 1
    while True:
        end, size = start, len(lines[start - 1])
        while end < len(lines) and end - start + 1 < window and size + len(lines[end]) <= max_chars:
            size += len(lines[end])  # lines[end] is line end + 1
            end += 1
        chunks.append(make_chunk(lines, "window", None, start, end))
        if end == len(lines):
            return chunks
        n = end - start + 1
        start = end + 1 - min(overlap, round(n * overlap / window), n - 1)
