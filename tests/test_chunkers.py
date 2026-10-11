import itertools
from pathlib import Path

import pytest

from grepwise.ingest.chunkers import (
    MAX_CHUNK_CHARS,
    MAX_LINE_CHARS,
    Chunk,
    size_windows,
    split_lines,
)
from grepwise.ingest.chunkers.code import chunk_code, chunk_file
from grepwise.ingest.chunkers.fallback import chunk_fallback

FIXTURES = Path(__file__).parent / "fixtures"
ALL_FIXTURE_FILES = sorted(p for p in FIXTURES.rglob("*") if p.is_file())


def shape(chunks: list[Chunk]) -> list[tuple[str, str | None, int, int]]:
    return [(c.kind, c.symbol, c.start_line, c.end_line) for c in chunks]


def chunks_for(name: str) -> list[Chunk]:
    path = FIXTURES / "chunkers" / name
    return chunk_file(name, path.read_text())[1]


def test_python_fixture_exact_chunks() -> None:
    assert shape(chunks_for("sample.py")) == [
        ("module", None, 1, 7),  # docstring, imports, constants
        ("function", "small_function", 10, 12),
        ("class", "SmallClass", 15, 20),  # includes the comment directly above it
        ("module", None, 23, 23),
        ("class", "BigClass", 26, 29),  # signature, docstring, class attribute
        ("method", "BigClass.method_one", 31, 78),
        ("class", "BigClass", 80, 80),  # class-level line between methods
        ("method", "BigClass.method_two", 82, 130),  # includes @property
        ("class", "BigClass.Nested", 132, 135),
        ("window", "huge_function", 138, 222),
        ("window", "huge_function", 210, 261),  # overlaps by 13 lines (~15% of 85)
        ("module", None, 264, 265),  # if __name__ == "__main__"
    ]


def test_typescript_fixture_exact_chunks() -> None:
    assert shape(chunks_for("sample.ts")) == [
        ("module", None, 1, 4),
        ("function", "smallFunction", 6, 9),  # includes its JSDoc comment
        ("function", "arrowHelper", 11, 11),  # `export const f = () => ...`
        ("module", None, 13, 15),  # interfaces are module code in M1
        ("class", "SmallClass", 17, 19),
        ("class", "BigClass", 21, 22),
        ("method", "BigClass.methodOne", 24, 72),
        ("class", "BigClass", 74, 74),
        ("method", "BigClass.methodTwo", 76, 124),
        ("window", "hugeFunction", 127, 207),
        ("window", "hugeFunction", 196, 240),
        ("module", None, 242, 242),
    ]


def test_methods_know_their_class() -> None:
    method = next(c for c in chunks_for("sample.py") if c.symbol == "BigClass.method_one")
    assert method.parent == "class BigClass"


@pytest.mark.parametrize("path", ALL_FIXTURE_FILES, ids=lambda p: str(p.relative_to(FIXTURES)))
def test_line_accuracy(path: Path) -> None:
    """The most important property of M1: content == the file's lines start..end."""
    text = path.read_text()
    lines = text.split("\n")
    _, chunks = chunk_file(path.name, text)
    assert chunks
    for c in chunks:
        expected = "\n".join(lines[c.start_line - 1 : c.end_line])
        assert c.content.rstrip("\n") == expected.rstrip("\n")
        assert c.content == "".join(split_lines(text)[c.start_line - 1 : c.end_line])


def test_chunks_respect_size_limit() -> None:
    for name in ("sample.py", "sample.ts"):
        assert all(len(c.content) <= MAX_CHUNK_CHARS for c in chunks_for(name))


def test_node_positions_are_stable() -> None:
    """Regression: tree-sitter 0.26.0 returned garbage rows on a second end_point read."""
    text = (FIXTURES / "chunkers" / "sample.py").read_text()
    assert shape(chunk_code(text, "python")) == shape(chunk_code(text, "python"))


def test_split_lines_only_splits_on_newline() -> None:
    assert split_lines("a\r\nb\x0cc\u2028d\ne") == ["a\r\n", "b\x0cc\u2028d\n", "e"]
    assert split_lines("a\n") == ["a\n"]
    assert split_lines("") == []


def test_crlf_files_keep_exact_content() -> None:
    text = "import os\r\n\r\ndef f():\r\n    return 1\r\n"
    assert shape(chunk_code(text, "python")) == [("module", None, 1, 1), ("function", "f", 3, 4)]
    assert chunk_code(text, "python")[1].content == "def f():\r\n    return 1\r\n"


def test_trailing_comment_does_not_pull_previous_code_into_function() -> None:
    text = "x = 1  # trailing\ndef f():\n    pass\n"
    assert shape(chunk_code(text, "python")) == [("module", None, 1, 1), ("function", "f", 2, 3)]


def test_js_export_default_and_anonymous_forms() -> None:
    text = "export default function () {\n  return 1;\n}\n"
    assert shape(chunk_code(text, "javascript")) == [("function", "default", 1, 3)]


def test_tsx_is_parsed() -> None:
    text = "export function App() {\n  return <div>hi</div>;\n}\n"
    assert shape(chunk_file("App.tsx", text)[1]) == [("function", "App", 1, 3)]


def test_parse_errors_fall_back_to_windows() -> None:
    text = "def broken(:\n    pass\n"
    assert shape(chunk_code(text, "python")) == [("window", None, 1, 2)]


def test_unsupported_language_uses_fallback() -> None:
    language, chunks = chunk_file("notes.md", "# Title\n\nbody\n")
    assert language is None
    assert shape(chunks) == [("window", None, 1, 3)]


def test_fallback_windows_are_80_lines_with_15_overlap() -> None:
    text = "".join(f"line {i}\n" for i in range(1, 201))
    assert shape(chunk_fallback(text)) == [
        ("window", None, 1, 80),
        ("window", None, 66, 145),
        ("window", None, 131, 200),
    ]


def test_empty_and_blank_files_have_no_chunks() -> None:
    assert chunk_file("a.py", "")[1] == []
    assert chunk_file("a.txt", "\n  \n")[1] == []


def test_size_windows_always_make_progress() -> None:
    lines = ["x" * 10_000 + "\n", "y\n", "z" * 10_000 + "\n"]
    assert size_windows(lines, 1, 3) == [(1, 1), (2, 2), (3, 3)]


def test_fallback_windows_are_capped_by_characters() -> None:
    text = "".join("x" * 999 + "\n" for _ in range(30))  # 1,000 chars per line
    chunks = chunk_fallback(text)
    assert all(len(c.content) <= MAX_CHUNK_CHARS for c in chunks)
    assert chunks[0].start_line == 1 and chunks[-1].end_line == 30
    for a, b in itertools.pairwise(chunks):
        assert a.start_line < b.start_line <= a.end_line + 1  # progress, no gaps


@pytest.mark.parametrize("name", ["consts.py", "notes.txt"])
def test_every_chunk_fits_when_every_line_fits(name: str) -> None:
    # The filter drops files with longer lines, so this covers everything we index.
    line = "x = '" + "y" * (MAX_LINE_CHARS - 6) + "'\n"
    assert len(line) - 1 <= MAX_LINE_CHARS
    chunks = chunk_file(name, line * 40)[1]
    assert chunks and all(len(c.content) <= MAX_CHUNK_CHARS for c in chunks)


def test_a_missing_grammar_is_downloaded_on_first_use(monkeypatch: pytest.MonkeyPatch) -> None:
    import tree_sitter_language_pack as tslp

    from grepwise.ingest.chunkers import code

    downloaded: list[list[str]] = []

    def fake_download(names: list[str]) -> int:
        downloaded.append(names)
        return len(names)

    monkeypatch.setattr(tslp, "downloaded_languages", lambda: ["python"])
    monkeypatch.setattr(tslp, "download", fake_download)
    code.ensure_grammar("python")
    assert downloaded == []  # already there
    code.ensure_grammar("tsx")
    assert downloaded == [["tsx"]]
