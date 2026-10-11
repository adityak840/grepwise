from pathlib import Path

import pytest

from grepwise.ingest import filters
from grepwise.ingest.chunkers import MAX_LINE_CHARS
from grepwise.ingest.filters import RepoTooLargeError, collect_files


def write(root: Path, rel: str, data: bytes | str = "x = 1\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data)
    else:
        path.write_bytes(data)


def kept(root: Path) -> list[str]:
    return [f.path for f in collect_files(root).kept]


def test_keeps_normal_text_files(tmp_path: Path) -> None:
    write(tmp_path, "src/app.py")
    write(tmp_path, "README.md", "# hi\n")
    assert kept(tmp_path) == ["README.md", "src/app.py"]


@pytest.mark.parametrize("folder", sorted(filters.SKIP_DIRS))
def test_skips_junk_folders(tmp_path: Path, folder: str) -> None:
    write(tmp_path, f"{folder}/inner/lib.js")
    write(tmp_path, f"src/{folder}/nested.js")  # at any depth
    write(tmp_path, "keep.py")
    assert kept(tmp_path) == ["keep.py"]


@pytest.mark.parametrize(
    "name",
    ["Cargo.lock", "package-lock.json", "pnpm-lock.yaml", "poetry.lock", "uv.lock"],
)
def test_skips_lockfiles(tmp_path: Path, name: str) -> None:
    write(tmp_path, name, "{}\n")
    result = collect_files(tmp_path)
    assert result.kept == [] and result.skipped == {"lockfile": 1}


@pytest.mark.parametrize("name", ["bundle.min.js", "style.min.css", "app.js.map"])
def test_skips_minified_and_source_maps(tmp_path: Path, name: str) -> None:
    write(tmp_path, name, "a\n")
    result = collect_files(tmp_path)
    assert result.kept == [] and result.skipped == {"generated": 1}


def test_skips_binary_files(tmp_path: Path) -> None:
    write(tmp_path, "logo.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00")
    write(tmp_path, "late_nul.txt", b"a" * 9_000 + b"\x00")  # NUL after the first 8 KB
    result = collect_files(tmp_path)
    assert result.kept == [] and result.skipped == {"binary": 2}


def test_skips_large_files(tmp_path: Path) -> None:
    write(tmp_path, "big.py", "x" * (filters.MAX_FILE_BYTES + 1))
    # Exactly at the limit, in short lines so the long-line rule doesn't apply.
    write(tmp_path, "ok.py", ("x" * 99 + "\n") * (filters.MAX_FILE_BYTES // 100))
    result = collect_files(tmp_path)
    assert [f.path for f in result.kept] == ["ok.py"]
    assert result.skipped == {"too_large": 1}


def test_skips_symlinks(tmp_path: Path) -> None:
    secret = tmp_path / "outside.txt"
    secret.write_text("secret\n")
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "link.txt").symlink_to(secret)
    assert kept(tmp_path / "repo") == []


def test_aborts_on_too_many_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(filters, "MAX_FILES", 3)
    for i in range(4):
        write(tmp_path, f"f{i}.py")
    with pytest.raises(RepoTooLargeError, match="more than 3 files"):
        collect_files(tmp_path)


def test_aborts_on_too_much_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(filters, "MAX_TOTAL_BYTES", 10)
    write(tmp_path, "a.py", "x" * 11)
    with pytest.raises(RepoTooLargeError, match="MB of text"):
        collect_files(tmp_path)


def test_skips_files_with_very_long_lines(tmp_path: Path) -> None:
    write(tmp_path, "data.json", '{"a": "' + "x" * MAX_LINE_CHARS + '"}\n')
    write(tmp_path, "ok.py", "x = 1\n" + "y" * MAX_LINE_CHARS + "\n")
    result = collect_files(tmp_path)
    assert [f.path for f in result.kept] == ["ok.py"]
    assert result.skipped == {"long_lines": 1}
