"""The reStructuredText splitter."""

from grepwise.ingest.chunkers import split_lines
from grepwise.ingest.chunkers.rst import chunk_rst, find_rst_headings

DOC = """\
.. _quickstart:

==========
Quickstart
==========

Intro to the guide.

Install
=======

Run pip.

Options
-------

Text about options::

    Not a heading
    =============

Required
~~~~~~~~

A required option.

+--------+-------+
| a      | b     |
+========+=======+

=====  =====
col    col
=====  =====

----

After a transition.
Still the same paragraph
------------------------
"""


def headings(text: str) -> list[tuple[int, int, str]]:
    return [(h.start, h.level, h.title) for h in find_rst_headings(split_lines(text))]


def test_headings_levels_follow_first_use() -> None:
    assert headings(DOC) == [
        (3, 1, "Quickstart"),  # overline + underline: its own style
        (9, 2, "Install"),
        (14, 3, "Options"),
        (22, 4, "Required"),
    ]  # not the indented literal block, the tables, the transition or the paragraph's last line


def test_sections_get_heading_paths() -> None:
    chunks = chunk_rst("docs/quickstart.rst", DOC)
    assert [c.symbol for c in chunks] == [
        "Quickstart",
        "Quickstart > Install",
        "Quickstart > Install > Options",
        "Quickstart > Install > Options > Required",
    ]  # the `.. _quickstart:` label alone doesn't make an intro section
    assert all(c.kind == "section" for c in chunks)
    first = chunks[0]
    assert (first.start_line, first.content.splitlines()[1]) == (3, "Quickstart")


def test_same_character_with_and_without_overline_are_different_levels() -> None:
    text = "=====\nTitle\n=====\n\nSub\n===\n\nbody\n"
    assert headings(text) == [(1, 1, "Title"), (5, 2, "Sub")]


def test_short_underline_and_inline_literals() -> None:
    assert headings("Too long a title\n====\n") == []
    assert headings("The ``Signer`` class\n====================\n") == [(1, 1, "The Signer class")]


def test_text_before_the_first_heading_becomes_an_intro() -> None:
    chunks = chunk_rst("index.rst", "Welcome text.\n\nGuide\n=====\n\nbody\n")
    assert [c.symbol for c in chunks] == ["index.rst", "Guide"]


def test_empty_and_heading_free_files() -> None:
    assert chunk_rst("a.rst", "\n\n") == []
    assert [c.symbol for c in chunk_rst("notes.rst", "just text\n")] == ["notes.rst"]


def test_docs_format_and_sphinx_txt() -> None:
    from grepwise.ingest.chunkers.docs import docs_format, sphinx_dirs

    paths = ["docs/conf.py", "docs/intro/install.txt", "README.md", "src/x.py", "NOTES.txt"]
    sphinx = sphinx_dirs(paths)
    assert sphinx == ["docs"]
    assert docs_format("docs/intro/install.txt", sphinx) == "rst"  # Django-style reST in .txt
    assert docs_format("NOTES.txt", sphinx) is None  # outside the Sphinx folder
    assert docs_format("README.md") == "markdown" and docs_format("docs/a.MDX") == "markdown"
    assert docs_format("guide.rst") == "rst" and docs_format("src/x.py", sphinx) is None
    assert sphinx_dirs(["conf.py", "a.txt"]) == [""]  # conf.py at the root
    assert docs_format("a.txt", [""]) == "rst"
