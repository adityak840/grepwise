"""The M3 tool layer, end to end on the test database (FakeEmbedder; no network)."""

import json
from typing import Any

import jsonschema
import psycopg
import pytest
from rich.console import Console
from typer.testing import CliRunner

from grepwise import cli
from grepwise.config import Settings, get_settings
from grepwise.ingest.embed import FakeEmbedder
from grepwise.ingest.pipeline import index_openapi, index_repo
from grepwise.search.hybrid import search as hybrid_search
from grepwise.tools import TOOLS, Tool, ToolContext, call_tool, is_error, serialize, tool_schemas
from grepwise.tools.find_symbol import _EXACT_SQL, signature
from grepwise.tools.get_endpoint import literal_segments, matches, normalize
from grepwise.tools.verify import VerifiedCitation, verify_citation
from tests.helpers import FIXTURES, REPO_URL, fake_fetcher

BIG_URL = "https://github.com/example/big"  # the chunker fixtures, as a repo


def settings(**overrides: Any) -> Settings:
    return get_settings().model_copy(update=overrides)


@pytest.fixture
def ctx(indexed: psycopg.Connection, embedder: FakeEmbedder) -> ToolContext:
    index_repo(indexed, embedder, BIG_URL, "1.0", fetcher=fake_fetcher(FIXTURES / "chunkers"))
    return ToolContext(indexed, settings(), embedder)


def call(ctx: ToolContext, tool: str, /, **args: Any) -> dict[str, Any]:
    result = call_tool(ctx, tool, args)
    assert len(serialize(result)) <= ctx.settings.tool_max_chars
    return result


def error_code(result: dict[str, Any]) -> str:
    assert is_error(result), result
    return str(result["error"]["code"])


# --- Registry ----------------------------------------------------------------------------


def test_registry_has_the_five_tools() -> None:
    assert list(TOOLS) == ["list_sources", "search", "read_file", "find_symbol", "get_endpoint"]


@pytest.mark.parametrize("schema", tool_schemas(), ids=lambda s: s["name"])
def test_schemas_are_valid_and_every_field_is_described(schema: dict[str, Any]) -> None:
    assert len(schema["description"]) > 40
    input_schema = schema["input_schema"]
    jsonschema.Draft202012Validator.check_schema(input_schema)
    assert input_schema["type"] == "object"
    for field, spec in input_schema["properties"].items():
        assert spec.get("description"), f"{schema['name']}.{field} has no description"
        assert "anyOf" not in spec  # optional fields are simplified for LLM tool APIs


def test_bad_arguments_name_the_field(ctx: ToolContext) -> None:
    wrong_type = call_tool(ctx, "search", {"query": "x", "limit": "many"})
    assert error_code(wrong_type) == "bad_input" and "limit" in wrong_type["error"]["message"]
    unknown = call_tool(ctx, "read_file", {"source": "a", "path": "b", "lines": "1-2"})
    assert error_code(unknown) == "bad_input" and "lines" in unknown["error"]["message"]
    missing = call_tool(ctx, "find_symbol", {})
    assert error_code(missing) == "bad_input" and "name" in missing["error"]["message"]
    assert error_code(call_tool(ctx, "search", ["not", "an", "object"])) == "bad_input"
    assert error_code(call_tool(ctx, "search", {"query": "x", "limit": 99})) == "bad_input"


def test_unknown_tool(ctx: ToolContext) -> None:
    result = call_tool(ctx, "delete_everything", {})
    assert error_code(result) == "bad_input" and "list_sources" in result["error"]["hint"]


def test_exceptions_become_internal_errors_without_tracebacks(
    ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_ctx: ToolContext, _inp: Any) -> Any:
        raise RuntimeError("secret detail at /home/me/project")

    original = TOOLS["list_sources"]
    broken = Tool("list_sources", original.description, original.input_model, boom)
    monkeypatch.setitem(TOOLS, "list_sources", broken)
    result = call_tool(ctx, "list_sources", {})
    assert error_code(result) == "internal"
    text = serialize(result)
    assert "Traceback" not in text and "secret detail" not in text
    assert not is_error(call_tool(ctx, "find_symbol", {"name": "Signer"}))  # conn still usable


# --- list_sources ------------------------------------------------------------------------


def test_list_sources(ctx: ToolContext) -> None:
    out = call(ctx, "list_sources")
    by_ref = {s["ref"]: s for s in out["sources"]}
    assert set(by_ref) == {"example/big@1.0", "example/docs_repo@v2", "example/mini_repo@v1",
                           "petstore@1.0.27"}  # fmt: skip
    for ref, s in by_ref.items():
        name, version = ref.split("@")
        docs, chunks = ctx.conn.execute(
            "SELECT (SELECT count(*) FROM documents d WHERE d.source_id = s.id), "
            "(SELECT count(*) FROM chunks c WHERE c.source_id = s.id) "
            "FROM sources s WHERE name = %s AND version = %s",
            (name, version),
        ).fetchone() or (None, None)
        assert (s["documents"], s["chunks"]) == (docs, chunks)
        assert s["embedding_model"] == "fake:768" and s["status"] == "ready"
    assert by_ref["example/docs_repo@v2"]["subpath"] == "docs"
    assert [s["ref"] for s in call(ctx, "list_sources", type="openapi")["sources"]] == [
        "petstore@1.0.27"
    ]


# --- search ------------------------------------------------------------------------------


def test_search_limit_and_found_by(ctx: ToolContext, embedder: FakeEmbedder) -> None:
    out = call(ctx, "search", query="pet", limit=3)
    assert len(out["results"]) == 3
    direct = hybrid_search(ctx.conn, embedder, "pet", limit=3, token_budget=None)
    for hit, r in zip(out["results"], direct, strict=True):
        assert hit["citation"] == r.citation
        assert hit["found_by"] == {"vector": r.vector_rank, "keyword": r.keyword_rank}


def test_search_token_budget_truncates(ctx: ToolContext) -> None:
    ctx.settings = settings(search_token_budget=300)
    out = call(ctx, "search", query="pet", limit=20)
    assert out["truncated"] is True
    assert sum(len(h["content"]) // 4 for h in out["results"]) <= 300 or len(out["results"]) == 1


def test_search_unknown_source(ctx: ToolContext) -> None:
    result = call(ctx, "search", query="x", sources=["example/mini_rep"])
    assert error_code(result) == "source_not_found"
    assert "example/mini_repo" in result["error"]["hint"]


# --- read_file ---------------------------------------------------------------------------


def lines_of(rel: str) -> list[str]:
    return (FIXTURES / rel).read_text().split("\n")


def test_read_code_exact_lines(ctx: ToolContext) -> None:
    out = call(ctx, "read_file", source="example/mini_repo@v1", path="src/minipkg/signer.py",
               start_line=7, end_line=11)  # fmt: skip
    assert out["content"] == "\n".join(lines_of("mini_repo/src/minipkg/signer.py")[6:11]) + "\n"
    assert out["citation"] == "example/mini_repo@v1:src/minipkg/signer.py:L7-L11"
    assert (out["start_line"], out["end_line"], out["truncated"]) == (7, 11, False)


def test_read_docs_and_openapi(ctx: ToolContext) -> None:
    docs = call(ctx, "read_file", source="example/docs_repo", path="docs/plain.md")
    assert docs["content"] == (FIXTURES / "docs_repo/docs/plain.md").read_text()
    assert docs["start_line"] == 1 and docs["total_lines"] == 3  # no range: from line 1
    api = call(ctx, "read_file", source="petstore", path="GET /pet/{petId}", start_line=1,
               end_line=3)  # fmt: skip
    assert api["content"] == "GET /pet/{petId}\nOperation: getPetById\nSummary: Find pet by ID\n"


def test_read_line_cap(ctx: ToolContext) -> None:
    ctx.settings = settings(read_max_lines=50)
    out = call(ctx, "read_file", source="example/big", path="sample.py")
    assert (out["start_line"], out["end_line"], out["total_lines"]) == (1, 50, 265)
    assert out["truncated"] is True and out["next_start_line"] == 51
    nxt = call(ctx, "read_file", source="example/big", path="sample.py", start_line=51)
    assert nxt["start_line"] == 51


def test_read_char_cap(ctx: ToolContext) -> None:
    out = call(ctx, "read_file", source="example/big", path="sample.py")  # 16 KB file
    assert out["truncated"] is True and out["end_line"] < 200
    assert out["next_start_line"] == out["end_line"] + 1
    assert out["citation"].endswith(f":L1-L{out['end_line']}")


def test_read_range_errors(ctx: ToolContext) -> None:
    past = call(ctx, "read_file", source="example/mini_repo", path="README.md", start_line=99)
    assert error_code(past) == "bad_line_range" and "3 lines" in past["error"]["hint"]
    backwards = call(ctx, "read_file", source="example/mini_repo", path="README.md",
                     start_line=3, end_line=2)  # fmt: skip
    assert error_code(backwards) == "bad_line_range"
    clamped = call(ctx, "read_file", source="example/mini_repo", path="README.md", end_line=99)
    assert clamped["end_line"] == 3 and clamped["truncated"] is False


def test_read_missing_file_suggests_paths(ctx: ToolContext) -> None:
    result = call(ctx, "read_file", source="example/mini_repo", path="src/minipkg/signr.py")
    assert error_code(result) == "file_not_found"
    assert "src/minipkg/signer.py" in result["error"]["hint"]


def test_read_ambiguous_source(ctx: ToolContext, embedder: FakeEmbedder) -> None:
    index_repo(ctx.conn, embedder, REPO_URL, "v2", fetcher=fake_fetcher(FIXTURES / "mini_repo"))
    result = call(ctx, "read_file", source="example/mini_repo", path="README.md")
    assert error_code(result) == "ambiguous_source"
    assert "example/mini_repo@v1" in result["error"]["hint"]
    assert "example/mini_repo@v2" in result["error"]["hint"]
    assert not is_error(call(ctx, "read_file", source="example/mini_repo@v2", path="README.md"))


def test_read_pre_m2_document(ctx: ToolContext) -> None:
    ctx.conn.execute("UPDATE documents SET content = NULL WHERE path = 'README.md'")
    result = call(ctx, "read_file", source="example/mini_repo", path="README.md")
    assert error_code(result) == "not_reindexed"


# --- find_symbol -------------------------------------------------------------------------


def symbols(result: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(m["symbol"], m["kind"], m["citation"]) for m in result["matches"]]


def test_find_qualified_and_last_part(ctx: ToolContext) -> None:
    qualified = call(ctx, "find_symbol", name="BigClass.method_one")
    assert symbols(qualified) == [
        ("BigClass.method_one", "method", "example/big@1.0:sample.py:L31-L78")
    ]
    assert qualified["matches"][0]["signature"] == "def method_one(self, item_0, item_1):"
    short = call(ctx, "find_symbol", name="methodOne")
    assert symbols(short) == [("BigClass.methodOne", "method", "example/big@1.0:sample.ts:L24-L72")]


def test_find_case_insensitive_fallback(ctx: ToolContext) -> None:
    out = call(ctx, "find_symbol", name="bigclass.METHOD_ONE")
    assert out["case_insensitive"] is True
    assert [m["symbol"] for m in out["matches"]] == ["BigClass.method_one"]
    exact = call(ctx, "find_symbol", name="Signer")
    assert exact["case_insensitive"] is False


def test_find_docs_section(ctx: ToolContext) -> None:
    out = call(ctx, "find_symbol", name="Required Options")
    assert [(m["symbol"], m["source"]) for m in out["matches"]] == [
        ("Tutorial > CLI Options > Required Options", "example/docs_repo@v2")
    ]
    assert out["matches"][0]["signature"] == "### Required Options"


def test_find_merges_windows_into_one_range(ctx: ToolContext) -> None:
    out = call(ctx, "find_symbol", name="huge_function")
    assert symbols(out) == [("huge_function", "window", "example/big@1.0:sample.py:L138-L261")]
    assert out["matches"][0]["signature"] == "def huge_function(items):"


def test_find_filters(ctx: ToolContext) -> None:
    both = call(ctx, "find_symbol", name="BigClass")
    assert {m["path"] for m in both["matches"]} == {"sample.py", "sample.ts"}
    filtered = call(ctx, "find_symbol", name="SmallClass", sources=["example/big@1.0"],
                   kinds=["class"])  # fmt: skip
    assert {m["path"] for m in filtered["matches"]} == {"sample.py", "sample.ts"}
    none = call(ctx, "find_symbol", name="BigClass", kinds=["function"])
    assert none["matches"] == []
    assert call(ctx, "find_symbol", name="Signer", sources=["petstore"])["matches"] == []


def test_find_symbol_uses_an_index(ctx: ToolContext) -> None:
    """With sequential scans disabled, the plan must still be possible through indexes."""
    with ctx.conn.transaction():
        ctx.conn.execute("SET LOCAL enable_seqscan = off")
        params = {"name": "sign", "n": 200, "f_names": None, "f_versions": None, "f_kinds": None}
        plan = "\n".join(row[0] for row in ctx.conn.execute(f"EXPLAIN {_EXACT_SQL}", params))
    assert "Seq Scan on chunks" not in plan
    assert "chunks_symbol_btree" in plan and "chunks_symbol_short" in plan


@pytest.mark.parametrize(
    ("kind", "content", "expected"),
    [
        ("method", "    @property\n    # note\n    def name(self):\n", "def name(self):"),
        ("function", "/** Docs. */\n// more\nexport function f() {}\n", "export function f() {}"),
        ("section", "\n## Heading\ntext\n", "## Heading"),
        ("endpoint", "GET /pet\nOperation: x\n", "GET /pet"),
    ],
)
def test_signature(kind: str, content: str, expected: str) -> None:
    assert signature(kind, content) == expected


# --- get_endpoint ------------------------------------------------------------------------


def endpoints(result: dict[str, Any]) -> list[str]:
    return [f"{e['method']} {e['path']}" for e in result["endpoints"]]


def test_endpoint_exact_template(ctx: ToolContext) -> None:
    out = call(ctx, "get_endpoint", method="GET", path="/pet/{petId}")
    assert endpoints(out) == ["GET /pet/{petId}"]
    assert out["endpoints"][0]["citation"] == "petstore@1.0.27:GET /pet/{petId}:L1-L27"
    assert out["endpoints"][0]["content"].startswith("GET /pet/{petId}\nOperation: getPetById")


def test_endpoint_concrete_path(ctx: ToolContext) -> None:
    assert endpoints(call(ctx, "get_endpoint", path="GET /pet/42")) == ["GET /pet/{petId}"]
    assert endpoints(call(ctx, "get_endpoint", method="get", path="/pet/42/?x=1")) == [
        "GET /pet/{petId}"
    ]


def test_endpoint_literal_beats_parameter(ctx: ToolContext) -> None:
    assert endpoints(call(ctx, "get_endpoint", path="GET /pet/findByStatus")) == [
        "GET /pet/findByStatus"
    ]


def test_endpoint_most_literal_template_wins(ctx: ToolContext, embedder: FakeEmbedder) -> None:
    spec = {
        "openapi": "3.0.0",
        "paths": {
            "/files/{a}/{b}": {"get": {"summary": "Generic"}},
            "/files/{name}/meta": {"get": {"summary": "Metadata"}},
        },
    }
    index_openapi(ctx.conn, embedder, "inline", "files", "1", loader=lambda _: spec)
    out = call(ctx, "get_endpoint", path="GET /files/report.pdf/meta", source="files")
    assert endpoints(out) == ["GET /files/{name}/meta"]
    assert literal_segments("/files/{name}/meta") == 3  # '', 'files', 'meta'


def test_endpoint_without_method_returns_all_methods(ctx: ToolContext) -> None:
    assert endpoints(call(ctx, "get_endpoint", path="/pet/7")) == [
        "DELETE /pet/{petId}",
        "GET /pet/{petId}",
    ]


def test_endpoint_not_found_has_a_hint(ctx: ToolContext) -> None:
    result = call(ctx, "get_endpoint", path="GET /pet/1/2/3")
    assert error_code(result) == "endpoint_not_found"
    assert "GET /pet/{petId}" in result["error"]["hint"]
    assert error_code(call(ctx, "get_endpoint", path="PATCH /pet/42")) == "endpoint_not_found"
    wrong_source = call(ctx, "get_endpoint", path="/pet/1", source="example/mini_repo")
    assert error_code(wrong_source) == "source_not_found"  # only openapi sources count


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ((None, "GET /pet/42"), ("GET", "/pet/42")),
        (("post", "pet/"), ("POST", "/pet")),
        ((None, "https://api.example.com/pet/1?x=2#f"), (None, "/pet/1")),
        ((None, "/"), (None, "/")),
        (("GET", "GET /pet/42"), ("GET", "/pet/42")),  # method in both fields
        (("get", "GET /pet/{petId}"), ("GET", "/pet/{petId}")),
    ],
)
def test_normalize(raw: tuple[str | None, str], expected: tuple[str | None, str]) -> None:
    assert normalize(*raw) == expected


def test_matches() -> None:
    assert matches("/pet/{petId}", "/pet/42")
    assert not matches("/pet/{petId}", "/pet/42/x")
    assert matches("/files/{name}.json", "/files/a.json")
    assert not matches("/files/{name}.json", "/files/a.xml")


# --- verify_citation ---------------------------------------------------------------------


def test_verify_valid_citations(ctx: ToolContext) -> None:
    for hit in call(ctx, "search", query="pet signer option", limit=20)["results"]:
        out = verify_citation(ctx.conn, hit["citation"])
        assert isinstance(out, VerifiedCitation), out
        assert out.content == hit["content"]


def test_read_and_verify_agree(ctx: ToolContext) -> None:
    citation = "petstore@1.0.27:GET /pet/{petId}:L1-L27"
    verified = verify_citation(ctx.conn, citation)
    read = call(ctx, "read_file", source="petstore@1.0.27", path="GET /pet/{petId}",
                start_line=1, end_line=27)  # fmt: skip
    assert isinstance(verified, VerifiedCitation)
    assert read["content"] == verified.content and read["citation"] == citation


@pytest.mark.parametrize(
    ("citation", "code"),
    [
        ("example/mini_repo@v1:src/nope.py:L1-L2", "file_not_found"),
        ("example/nothing@v1:README.md:L1-L2", "source_not_found"),
        ("example/mini_repo@v9:README.md:L1-L2", "source_not_found"),
        ("example/mini_repo@v1:README.md:L1-L99", "bad_line_range"),
        ("README.md lines 1 to 3", "bad_input"),
        ("example/mini_repo@v1:README.md:L0-L1", "bad_input"),
    ],
)
def test_verify_errors(ctx: ToolContext, citation: str, code: str) -> None:
    out = verify_citation(ctx.conn, citation)
    assert not isinstance(out, VerifiedCitation)
    assert out.error.code == code


# --- Caps --------------------------------------------------------------------------------


@pytest.mark.parametrize("cap", [1_500, 4_000, 12_000])
def test_no_output_exceeds_the_cap(ctx: ToolContext, cap: int) -> None:
    ctx.settings = settings(tool_max_chars=cap)
    calls: list[tuple[str, dict[str, Any]]] = [
        ("list_sources", {}),
        ("search", {"query": "pet report metric", "limit": 20}),
        ("read_file", {"source": "example/big", "path": "sample.py"}),
        ("read_file", {"source": "petstore", "path": "GET /reports/big"}),
        ("find_symbol", {"name": "BigClass"}),
        ("get_endpoint", {"path": "/reports/big"}),
        ("get_endpoint", {"path": "/pet/{petId}"}),
    ]
    for name, args in calls:
        result = call_tool(ctx, name, args)
        assert len(serialize(result)) <= cap, (name, len(serialize(result)))
        assert not is_error(result), result


# --- CLI ---------------------------------------------------------------------------------


@pytest.fixture
def runner(
    indexed: psycopg.Connection, empty_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> CliRunner:
    monkeypatch.setenv("DATABASE_URL", empty_db_url)
    monkeypatch.setenv("EMBEDDER", "fake")
    get_settings.cache_clear()
    monkeypatch.setattr(cli, "console", Console(width=300))
    return CliRunner()


def result_rows(output: str) -> int:
    return sum(1 for line in output.splitlines() if line.startswith("│ ") and "@" in line)


def test_cli_search_budget_only_when_asked(runner: CliRunner) -> None:
    unbudgeted = runner.invoke(cli.app, ["search", "pet", "--limit", "8"])
    assert unbudgeted.exit_code == 0, unbudgeted.output
    assert result_rows(unbudgeted.output) == 8
    budgeted = runner.invoke(cli.app, ["search", "pet", "--limit", "8", "--budget", "200"])
    assert budgeted.exit_code == 0
    assert 1 <= result_rows(budgeted.output) < 8
    get_settings.cache_clear()


def test_cli_tool_prints_raw_json_under_the_cap(runner: CliRunner) -> None:
    out = runner.invoke(cli.app, ["tool", "search", '{"query": "required option", "limit": 3}'])
    assert out.exit_code == 0, out.output
    data = json.loads(out.output)
    assert len(data["results"]) <= 3 and len(out.output.strip()) <= 12_000
    get_settings.cache_clear()


def test_cli_read_and_verify(runner: CliRunner) -> None:
    read = runner.invoke(cli.app, ["read", "petstore", "GET /pet/{petId}", "--lines", "1-3"])
    assert read.exit_code == 0 and "Operation: getPetById" in read.output
    verify = runner.invoke(cli.app, ["verify", "petstore@1.0.27:GET /pet/{petId}:L1-L27"])
    assert verify.exit_code == 0 and "valid" in verify.output
    bad = runner.invoke(cli.app, ["verify", "petstore@1.0.27:GET /nope:L1-L2"])
    assert bad.exit_code == 1 and "file_not_found" in bad.output
    get_settings.cache_clear()


# --- Review fixes ------------------------------------------------------------------------


def test_endpoint_method_in_both_fields(ctx: ToolContext) -> None:
    # What an agent does after copying 'GET /pet/{petId}' from a citation.
    out = call(ctx, "get_endpoint", method="GET", path="GET /pet/42")
    assert endpoints(out) == ["GET /pet/{petId}"]


def test_endpoint_conflicting_methods_is_bad_input(ctx: ToolContext) -> None:
    out = call(ctx, "get_endpoint", method="POST", path="GET /pet/42")
    assert error_code(out) == "bad_input"
    assert "POST" in out["error"]["message"] and "GET" in out["error"]["message"]


def test_schema_cleaning_keeps_a_field_named_title() -> None:
    from pydantic import BaseModel, Field

    from grepwise.tools import _clean_schema

    class WithTitle(BaseModel):
        title: str = Field(description="A field that happens to be called title")

    schema = _clean_schema(WithTitle.model_json_schema())
    assert "title" in schema["properties"] and schema["required"] == ["title"]
    assert "title" not in schema  # the model's own title is still removed
    assert "title" not in schema["properties"]["title"]  # and the field's display title


@pytest.mark.parametrize(
    "argv",
    [
        ["read", "petstore@1.0.27", "GET /pet/{petId}", "--lines", "0-5"],
        ["symbol", ""],
        ["endpoint", ""],
    ],
)
def test_cli_bad_input_is_a_one_line_error(runner: CliRunner, argv: list[str]) -> None:
    out = runner.invoke(cli.app, argv)
    assert out.exit_code != 0
    assert out.exception is None or isinstance(out.exception, SystemExit), out.exception
    assert "bad_input" in out.output and "Traceback" not in out.output
    get_settings.cache_clear()
