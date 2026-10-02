import pytest

from grepwise.search.citations import CitationError, format_citation, parse_citation


@pytest.mark.parametrize(
    ("name", "version", "path", "start", "end"),
    [
        ("fastapi/typer", "0.27.2", "docs/tutorial/options/required.md", 1, 24),
        ("petstore", "1.0.27", "GET /pet/{petId}", 1, 42),
        ("petstore", "1.0.27", "GET /files/{name}:download", 3, 9),  # ':' in the path
        ("pallets/itsdangerous", "2.2.0", "src/my file.py", 10, 10),  # space in the path
    ],
)
def test_round_trip(name: str, version: str, path: str, start: int, end: int) -> None:
    text = format_citation(name, version, path, start, end)
    c = parse_citation(text)
    assert (c.name, c.version, c.path, c.start_line, c.end_line) == (
        name, version, path, start, end,
    )  # fmt: skip
    assert str(c) == text
    assert c.source == f"{name}@{version}"


def test_example_format() -> None:
    assert format_citation("petstore", "1.0.27", "GET /pet/{petId}", 1, 42) == (
        "petstore@1.0.27:GET /pet/{petId}:L1-L42"
    )


@pytest.mark.parametrize(
    "text",
    ["", "petstore:GET /x:L1-L2", "a@1:path:L5-L2", "a@1:path:L0-L2", "a@1:path:L1", "a@1::L1-L2"],
)
def test_rejects_malformed(text: str) -> None:
    with pytest.raises(CitationError):
        parse_citation(text)
