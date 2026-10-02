from pathlib import Path

import pytest

from grepwise.ingest.chunkers import MAX_CHUNK_CHARS, Chunk, split_lines
from grepwise.ingest.chunkers.markdown import chunk_markdown

DOCS = Path(__file__).parent / "fixtures" / "docs_repo"
DOC_FILES = sorted(p for p in DOCS.rglob("*") if p.suffix in (".md", ".mdx"))


def shape(chunks: list[Chunk]) -> list[tuple[str, str | None, int, int]]:
    return [(c.kind, c.symbol, c.start_line, c.end_line) for c in chunks]


def chunks_for(rel: str) -> list[Chunk]:
    return chunk_markdown(rel, (DOCS / rel).read_text())


def test_markdown_fixture_exact_sections() -> None:
    assert shape(chunks_for("docs/tutorial/options.md")) == [
        ("section", "CLI Options", 1, 6),  # front matter + intro, named by `title:`
        ("section", "Tutorial > CLI Options", 8, 12),  # '# Tutorial' has no body: merged
        ("section", "Tutorial > CLI Options > Required Options", 14, 22),  # code-block '#'
        ("section", "Tutorial > Advanced > Callbacks", 24, 27),  # '## Advanced' merged
        ("section", "Tutorial > Reference", 29, 103),  # oversized: split into windows
        ("section", "Tutorial > Reference", 93, 110),
        ("section", "Tutorial > Empty At End", 112, 112),  # no subheading to merge into
    ]


def test_mdx_fixture_exact_sections() -> None:
    assert shape(chunks_for("docs/components.mdx")) == [
        ("section", "components.mdx", 1, 4),  # MDX imports + intro, named by file
        ("section", "Using Tabs", 6, 10),  # inline code formatting stripped
        ("section", "Setext Heading", 12, 15),
    ]


def test_file_without_headings_is_one_section() -> None:
    assert shape(chunks_for("docs/plain.md")) == [("section", "plain.md", 1, 3)]


def test_hash_in_code_block_is_not_a_heading() -> None:
    required = chunks_for("docs/tutorial/options.md")[2]
    assert "# This is a comment, not a heading" in required.content


def test_merged_heading_title_stays_in_the_chunk() -> None:
    callbacks = chunks_for("docs/tutorial/options.md")[3]
    assert callbacks.content.startswith("## Advanced\n### Callbacks\n")


@pytest.mark.parametrize("path", DOC_FILES, ids=lambda p: str(p.relative_to(DOCS)))
def test_line_accuracy(path: Path) -> None:
    text = path.read_text()
    lines = split_lines(text)
    for c in chunk_markdown(path.name, text):
        assert c.content == "".join(lines[c.start_line - 1 : c.end_line])
        assert len(c.content) <= MAX_CHUNK_CHARS


def test_front_matter_is_not_mistaken_for_a_setext_heading() -> None:
    text = "---\ntitle: Hello\n---\n\nBody.\n"
    assert shape(chunk_markdown("a.md", text)) == [("section", "Hello", 1, 5)]


def test_leading_horizontal_rule_is_not_front_matter() -> None:
    text = "---\n\n# Install\n\nRun it.\n\n---\n\n## Usage\n\nUse it.\n"
    assert [c.symbol for c in chunk_markdown("guide.md", text)] == ["Install", "Install > Usage"]


def test_unterminated_front_matter_is_plain_text() -> None:
    text = "---\ntitle: Hello\n\n# Real\n\nBody\n"
    assert [c.symbol for c in chunk_markdown("a.md", text)][-1] == "Real"


def test_heading_formatting_is_stripped() -> None:
    text = "# The **bold** and `code` [link](x)\n\nBody\n"
    assert chunk_markdown("a.md", text)[0].symbol == "The bold and code link"


def test_chained_empty_headings_merge_into_the_deepest() -> None:
    text = "# A\n## B\n### C\n\nBody\n"
    assert shape(chunk_markdown("a.md", text)) == [("section", "A > B > C", 1, 5)]


def test_sibling_after_deeper_heading_resets_the_path() -> None:
    text = "# A\nx\n## B\ny\n### C\nz\n## D\nw\n"
    assert [c.symbol for c in chunk_markdown("a.md", text)] == ["A", "A > B", "A > B > C", "A > D"]


def test_empty_file_has_no_sections() -> None:
    assert chunk_markdown("a.md", "\n\n") == []
