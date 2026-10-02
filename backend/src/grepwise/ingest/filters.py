"""Decide which files in a checkout are worth indexing."""

import fnmatch
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from grepwise.ingest.chunkers import MAX_LINE_CHARS

SKIP_DIRS = frozenset(
    {".git", "node_modules", "vendor", "dist", "build", ".next", "__pycache__",
     ".venv", "venv", "target", "coverage"}
)  # fmt: skip
SKIP_FILE_PATTERNS = (
    "*.lock", "package-lock.json", "pnpm-lock.yaml", "poetry.lock", "uv.lock",
    "*.min.js", "*.min.css", "*.map",
)  # fmt: skip
MAX_FILE_BYTES = 200 * 1024
BINARY_SNIFF_BYTES = 8 * 1024

# Repo-wide limits, checked after filtering.
MAX_FILES = 5_000
MAX_TOTAL_BYTES = 50 * 1024 * 1024


class RepoTooLargeError(Exception):
    pass


@dataclass(frozen=True)
class SourceFile:
    path: str  # relative to the repo root, always with '/'
    text: str


@dataclass
class FilterResult:
    kept: list[SourceFile] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)  # reason -> count


def skip_reason_for_name(name: str) -> str | None:
    """Reason to skip a file based on its name alone (lockfile, minified, source map)."""
    for pattern in SKIP_FILE_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return "lockfile" if "lock" in pattern else "generated"
    return None


def is_binary(head: bytes) -> bool:
    return b"\x00" in head[:BINARY_SNIFF_BYTES]


def read_text(path: Path) -> tuple[str | None, str | None]:
    """Return (text, None) for an indexable file, or (None, skip_reason)."""
    if path.is_symlink() or not path.is_file():
        return None, "not_regular_file"
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        return None, "too_large"
    data = path.read_bytes()
    # A NUL anywhere would also be rejected by Postgres text columns.
    if is_binary(data) or b"\x00" in data:
        return None, "binary"
    text = data.decode("utf-8", errors="replace")
    if any(len(line) > MAX_LINE_CHARS for line in text.split("\n")):
        return None, "long_lines"  # data, minified or generated; would overflow a chunk
    return text, None


def collect_files(root: Path) -> FilterResult:
    """Walk `root`, skipping junk folders and files. Files are returned sorted by path."""
    result = FilterResult()
    total_bytes = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in place so os.walk never descends into skipped folders.
        for d in list(dirnames):
            if d in SKIP_DIRS:
                dirnames.remove(d)
                result.skipped["skipped_dir"] += 1
        for name in filenames:
            path = Path(dirpath) / name
            reason = skip_reason_for_name(name)
            text = None
            if reason is None:
                text, reason = read_text(path)
            if reason is not None or text is None:
                result.skipped[reason or "unreadable"] += 1
                continue
            rel = path.relative_to(root).as_posix()
            result.kept.append(SourceFile(path=rel, text=text))
            total_bytes += len(text.encode())
            if len(result.kept) > MAX_FILES:
                raise RepoTooLargeError(f"Repo has more than {MAX_FILES:,} files after filtering.")
            if total_bytes > MAX_TOTAL_BYTES:
                raise RepoTooLargeError(
                    f"Repo has more than {MAX_TOTAL_BYTES // (1024 * 1024)} MB of text."
                )
    result.kept.sort(key=lambda f: f.path)
    return result
