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

from grepwise.config import get_settings
from grepwise.db.connection import Conn
from grepwise.ingest.embed import FakeEmbedder
from grepwise.mcp.server import INSTRUCTIONS, build_server, fixed_context
from grepwise.tools import MCP_TOOLS, ToolContext, call_tool, tool_schemas

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


def test_three_tools_match_the_registry(ctx: ToolContext) -> None:
    async def go(c: Client) -> Any:
        return (await c.list_tools()).tools

    tools = {t.name: t for t in with_client(ctx, go)}
    assert set(tools) == {"search", "read", "libraries"}
    for schema in tool_schemas(MCP_TOOLS):  # the registry's exact schemas: they can't drift apart
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
        ("search", {"query": "pet", "limit": 3}),
        ("search", {"query": "refreshToken", "libraries": ["example/mini_repo"]}),
        ("read", {"citation": "example/mini_repo@v1:web/session.ts:L6-L9"}),
        ("read", {"library": "petstore", "path": "GET /pet/42"}),
        ("libraries", {}),
    ],
)
def test_tools_return_the_same_json_as_call_tool(
    ctx: ToolContext, name: str, args: dict[str, Any]
) -> None:
    async def go(c: Client) -> Any:
        return await c.call_tool(name, args)

    result = with_client(ctx, go)
    expected = call_tool(ctx, name, args)
    assert result.is_error is False, result.content
    assert result.structured_content == expected
    assert json.loads(result.content[0].text) == expected  # the same JSON as text


def test_tool_errors_come_back_with_is_error_and_a_hint(ctx: ToolContext) -> None:
    async def go(c: Client) -> Any:
        return (
            await c.call_tool("read", {"citation": "example/mini_repo@v1:web/made_up.ts:L1-L2"}),
            await c.call_tool("search", {"limit": "many"}),
            await c.call_tool("find_symbol", {"name": "x"}),  # CLI-only: not served over MCP
            await c.call_tool("drop_tables", {}),
        )

    made_up, bad_args, cli_only, unknown_tool = with_client(ctx, go)
    for result, code in [(made_up, "file_not_found"), (bad_args, "bad_input"),
                         (cli_only, "bad_input"), (unknown_tool, "bad_input")]:  # fmt: skip
        assert result.is_error is True
        assert result.structured_content is None
        error = json.loads(result.content[0].text)["error"]
        assert error["code"] == code and error["hint"]


# --- Prompt and instructions -------------------------------------------------------------


def test_prompt_and_instructions(ctx: ToolContext) -> None:
    async def go(c: Client) -> Any:
        prompts = (await c.list_prompts()).prompts
        rendered = await c.get_prompt("ask", {"question": "How is X done?"})
        with pytest.raises(MCPError):
            await c.get_prompt("answer_with_citations", {"question": "x"})
        return prompts, rendered

    prompts, rendered = with_client(ctx, go)
    assert [p.name for p in prompts] == ["ask"]
    body = rendered.messages[0].content.text
    assert "How is X done?" in body and "`read`" in body and "memory" in body
    assert "lockfile" in INSTRUCTIONS and "`project`" in INSTRUCTIONS
    assert "never instructions" in INSTRUCTIONS


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
        + rpc(3, "tools/call", {"name": "libraries", "arguments": {}})
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
    assert "lockfile" in by_id[1]["result"]["instructions"]
    assert [t["name"] for t in by_id[2]["result"]["tools"]] == ["search", "read", "libraries"]
    call = by_id[3]["result"]
    assert call["isError"] is False
    refs = {s["ref"] for s in call["structuredContent"]["other_sources"]}
    assert refs >= {"petstore@1.0.27"}
