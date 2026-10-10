"""The MCP server: an in-memory client and a real stdio subprocess. Offline."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.client.client import Client
from mcp.shared.exceptions import MCPError
from mcp.types import TextResourceContents

from grepwise.config import get_settings
from grepwise.db.connection import Conn
from grepwise.ingest.embed import FakeEmbedder
from grepwise.mcp.server import SOURCES_URI, build_server, fixed_context, source_uri
from grepwise.tools import ToolContext, call_tool, tool_schemas

HANDSHAKE_VERSION = "2025-11-25"  # the classic initialize handshake most clients use today


@pytest.fixture
def ctx(indexed: Conn, embedder: FakeEmbedder) -> ToolContext:
    return ToolContext(indexed, get_settings(), embedder)


def with_client(ctx: ToolContext, fn: Any) -> Any:
    """Run `await fn(client)` against an in-memory MCP client."""

    async def main() -> Any:
        async with Client(build_server(lambda: fixed_context(ctx))) as client:
            return await fn(client)

    return anyio.run(main)


# --- Tools -------------------------------------------------------------------------------


def test_tools_match_the_registry(ctx: ToolContext) -> None:
    async def go(c: Client) -> Any:
        return (await c.list_tools()).tools

    tools = {t.name: t for t in with_client(ctx, go)}
    assert set(tools) == {"list_sources", "search", "read_file", "find_symbol", "get_endpoint",
                          "verify_citation"}  # fmt: skip
    for schema in tool_schemas():  # the agent's exact schemas: the two can't drift apart
        tool = tools[schema["name"]]
        assert tool.input_schema == schema["input_schema"]
        assert tool.description == schema["description"]
    for tool in tools.values():
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.open_world_hint is False


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("list_sources", {}),
        ("search", {"query": "pet", "limit": 3}),
        ("read_file", {"source": "example/mini_repo@v1", "path": "README.md"}),
        ("find_symbol", {"name": "Signer"}),
        ("get_endpoint", {"path": "GET /pet/42"}),
    ],
)
def test_tools_return_the_same_json_as_call_tool(
    ctx: ToolContext, name: str, args: dict[str, Any]
) -> None:
    async def go(c: Client) -> Any:
        return await c.call_tool(name, args)

    result = with_client(ctx, go)
    expected = call_tool(ctx, name, args)
    assert result.is_error is False
    assert result.structured_content == expected
    assert json.loads(result.content[0].text) == expected  # the same JSON as text


def test_tool_errors_come_back_with_is_error_and_a_hint(ctx: ToolContext) -> None:
    async def go(c: Client) -> Any:
        return (
            await c.call_tool("read_file", {"source": "nope", "path": "x"}),
            await c.call_tool("search", {"limit": "many"}),
            await c.call_tool("drop_tables", {}),
        )

    unknown_source, bad_args, unknown_tool = with_client(ctx, go)
    for result, code in [(unknown_source, "source_not_found"), (bad_args, "bad_input"),
                         (unknown_tool, "bad_input")]:  # fmt: skip
        assert result.is_error is True
        assert result.structured_content is None
        error = json.loads(result.content[0].text)["error"]
        assert error["code"] == code and error["hint"]


def test_verify_citation(ctx: ToolContext) -> None:
    async def go(c: Client) -> Any:
        return [
            await c.call_tool("verify_citation", {"citation": cite})
            for cite in [
                "example/mini_repo@v1:web/session.ts:L6-L9",
                "example/mini_repo@v1:web/made_up.ts:L1-L2",
                "session.ts lines 6 to 9",
            ]
        ] + [await c.call_tool("verify_citation", {"citation": 42})]

    valid, made_up, malformed, wrong_type = with_client(ctx, go)
    assert valid.is_error is False
    assert valid.structured_content["content"].startswith("export async function refreshToken")
    assert json.loads(made_up.content[0].text)["error"]["code"] == "file_not_found"
    for result in (malformed, wrong_type):
        assert result.is_error
        assert json.loads(result.content[0].text)["error"]["code"] == "bad_input"


# --- Resources and prompt ----------------------------------------------------------------


def test_resources(ctx: ToolContext) -> None:
    async def go(c: Client) -> Any:
        listed = [str(r.uri) for r in (await c.list_resources()).resources]
        templates = (await c.list_resource_templates()).resource_templates

        async def read(uri: str) -> Any:
            content = (await c.read_resource(uri)).contents[0]
            assert isinstance(content, TextResourceContents)
            return json.loads(content.text)

        sources = await read(SOURCES_URI)
        one = await read(source_uri("example/mini_repo@v1"))
        api = await read(source_uri("petstore@1.0.27"))
        return listed, templates, sources, one, api

    listed, templates, sources, one, api = with_client(ctx, go)
    assert SOURCES_URI in listed
    assert "grepwise://sources/example%2Fmini_repo@v1" in listed  # '/' is URL-encoded
    assert templates[0].uri_template == "grepwise://sources/{name}@{version}"
    assert {s["ref"] for s in sources["sources"]} == {
        "example/mini_repo@v1", "example/docs_repo@v2", "petstore@1.0.27",
    }  # fmt: skip
    assert one["ref"] == "example/mini_repo@v1"
    (repo,) = one["sources"]
    assert (repo["type"], repo["documents"]) == ("repo", 5)
    assert {"folder": "web/", "files": 2} in repo["folders"]
    assert {"folder": "(root)", "files": 1} in repo["folders"]
    assert {"folder": "/pet", "files": 5} in api["sources"][0]["folders"]


def test_unknown_resource_is_not_found(ctx: ToolContext) -> None:
    async def go(c: Client) -> Any:
        with pytest.raises(MCPError) as err:
            await c.read_resource(source_uri("example/nothing@v1"))
        return err.value

    error = with_client(ctx, go)
    assert "Resource not found" in str(error) and "example/mini_repo@v1" in str(error)


def test_prompt_includes_the_question(ctx: ToolContext) -> None:
    async def go(c: Client) -> Any:
        prompts = (await c.list_prompts()).prompts
        rendered = await c.get_prompt("answer_with_citations", {"question": "How is X done?"})
        return prompts, rendered

    prompts, rendered = with_client(ctx, go)
    assert [p.name for p in prompts] == ["answer_with_citations"]
    body = rendered.messages[0].content.text
    assert "How is X done?" in body and "verify_citation" in body and "data" in body


# --- stdio -------------------------------------------------------------------------------


def rpc(id_: int | None, method: str, params: dict[str, Any] | None = None) -> str:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if id_ is not None:
        msg["id"] = id_
    return json.dumps(msg) + "\n"


def test_stdio_smoke_stdout_carries_only_protocol(indexed: Conn, db_path: Path) -> None:
    script = Path(sys.executable).parent / "grepwise"
    env = {**os.environ, "GREPWISE_HOME": str(db_path.parent), "GREPWISE_EMBEDDER": "fake"}
    env.pop("VIRTUAL_ENV", None)
    hello = {
        "protocolVersion": HANDSHAKE_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "smoke-test", "version": "1"},
    }
    messages = (
        rpc(1, "initialize", hello)
        + rpc(None, "notifications/initialized")
        + rpc(2, "tools/list")
        + rpc(3, "tools/call", {"name": "list_sources", "arguments": {}})
    )
    proc = subprocess.Popen(
        [str(script), "mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=env,
    )  # fmt: skip
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(messages)
    proc.stdin.flush()
    lines: list[str] = []
    while not any('"id":3' in line.replace(" ", "") for line in lines):
        line = proc.stdout.readline()  # like a real client: wait for the replies...
        assert line, f"server exited early: {proc.stderr.read() if proc.stderr else ''}"
        lines.append(line)
    proc.stdin.close()  # ...then close the connection
    assert proc.wait(timeout=30) == 0, proc.stderr.read() if proc.stderr else ""  # clean exit
    rest = proc.stdout.read()
    lines += rest.splitlines()
    replies = [json.loads(line) for line in lines if line.strip()]  # a stray print fails here
    assert all(r.get("jsonrpc") == "2.0" for r in replies)
    by_id = {r["id"]: r for r in replies if "id" in r}
    assert by_id[1]["result"]["serverInfo"]["name"] == "grepwise"
    assert len(by_id[2]["result"]["tools"]) == 6
    call = by_id[3]["result"]
    assert call["isError"] is False
    assert {s["ref"] for s in call["structuredContent"]["sources"]} >= {"petstore@1.0.27"}
