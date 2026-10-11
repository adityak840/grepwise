"""The tool layer. Three tools are served over MCP (`MCP_TOOLS`: libraries, search, read);
the rest stay for the CLI and `grepwise tool`.

`call_tool` is the one entry point: it validates arguments, runs the tool, and caps the
output. It never raises; every problem comes back as {"error": {code, message, hint}}.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from grepwise.tools.caps import serialize, shrink_to_fit
from grepwise.tools.context import ToolContext
from grepwise.tools.errors import ToolError, ToolErrorBody
from grepwise.tools.find_symbol import FindSymbolInput, find_symbol
from grepwise.tools.get_endpoint import GetEndpointInput, get_endpoint
from grepwise.tools.libraries import LibrariesInput, libraries
from grepwise.tools.read import ReadInput, read
from grepwise.tools.read_file import ReadFileInput, read_file
from grepwise.tools.search import SearchInput, search
from grepwise.tools.sources import ListSourcesInput, list_sources

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str  # written for an LLM: what it does and when to use it
    input_model: type[BaseModel]
    run: Callable[[ToolContext, Any], BaseModel]


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            "search",
            "Search the real code and docs of the project's libraries, at the exact versions it "
            "locks. Use it before writing, changing or explaining code that uses a third-party "
            "library, instead of relying on memory. Name libraries plainly (['typer']); one "
            "that isn't indexed yet is indexed on the spot. Without `libraries`, it searches "
            "the project's dependencies. Returns ranked snippets, each with a citation and a "
            "link.",
            SearchInput,
            search,
        ),
        Tool(
            "read",
            "Read exact lines, numbered, by citation (from a search result) or by library and "
            "path, e.g. to see the whole function around a result. Also checks a citation: an "
            "unknown source, path or line range is an error.",
            ReadInput,
            read,
        ),
        Tool(
            "libraries",
            "List the project's dependencies with their locked versions and whether each is "
            "indexed (search indexes missing ones automatically), plus other indexed sources.",
            LibrariesInput,
            libraries,
        ),
        # For the CLI and `grepwise tool`; not served over MCP.
        Tool("list_sources", "List every indexed source (code repos, docs, API specs) with its "
             "version, status and counts.", ListSourcesInput, list_sources),
        Tool("read_file", "Read exact lines of an indexed file (or an API endpoint) by source "
             "ref and path.", ReadFileInput, read_file),
        Tool("find_symbol", "Find where a function, class, method or docs section is defined, "
             "by exact name.", FindSymbolInput, find_symbol),
        Tool("get_endpoint", "Get an API endpoint's full description (parameters, body, "
             "responses) by template or real path.", GetEndpointInput, get_endpoint),
    ]
}  # fmt: skip

MCP_TOOLS = ("search", "read", "libraries")


def _clean_schema(node: Any) -> Any:
    """Simplify Pydantic's JSON Schema for LLM tool APIs: drop titles, and turn
    `anyOf: [X, null]` (from optional fields) into plain X."""
    if isinstance(node, list):
        return [_clean_schema(n) for n in node]
    if not isinstance(node, dict):
        return node
    any_of = node.get("anyOf")
    if isinstance(any_of, list):
        non_null = [s for s in any_of if s != {"type": "null"}]
        if len(non_null) == 1 and len(non_null) < len(any_of):
            merged = {**{k: v for k, v in node.items() if k != "anyOf"}, **non_null[0]}
            return _clean_schema(merged)
    out: dict[str, Any] = {}
    for k, v in node.items():
        if k == "title":
            continue  # a display title, not data
        if k == "properties" and isinstance(v, dict):
            # Keys here are field names: keep them all, even a field called "title".
            out[k] = {field: _clean_schema(sub) for field, sub in v.items()}
        else:
            out[k] = _clean_schema(v)
    return out


def tool_schemas(names: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": _clean_schema(t.input_model.model_json_schema()),
        }
        for t in TOOLS.values()
        if names is None or t.name in names
    ]


def _error(code: str, message: str, hint: str) -> dict[str, Any]:
    body = ToolErrorBody.model_validate({"code": code, "message": message, "hint": hint})
    return ToolError(error=body).model_dump()


def describe_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:3]:
        field = ".".join(str(p) for p in err["loc"]) or "arguments"
        parts.append(f"{field}: {err['msg']}")
    return "; ".join(parts)


def call_tool(ctx: ToolContext, name: str, args: Any) -> dict[str, Any]:
    """Validate `args`, run the tool and cap the output. Never raises."""
    tool = TOOLS.get(name)
    if tool is None:
        return _error("bad_input", f"Unknown tool {name!r}.", f"Tools: {', '.join(TOOLS)}.")
    try:
        inp = tool.input_model.model_validate(args if args is not None else {})
    except ValidationError as exc:
        return _error(
            "bad_input",
            f"Invalid arguments for {name}: {describe_validation_error(exc)}.",
            "Fix the named field(s) to match the tool's input schema and call again.",
        )
    try:
        output = tool.run(ctx, inp)
    except Exception:
        log.exception("Tool %s failed", name)  # traceback goes to the log, never the output
        # Never leave a transaction open for the next call.
        if ctx.conn.in_transaction:
            ctx.conn.rollback()
        return _error(
            "internal",
            f"{name} failed unexpectedly.",
            "Try again, or use a different tool or arguments.",
        )
    return shrink_to_fit(output.model_dump(mode="json"), ctx.settings.tool_max_chars)


def is_error(result: dict[str, Any]) -> bool:
    return set(result) == {"error"}


__all__ = [
    "MCP_TOOLS",
    "TOOLS",
    "Tool",
    "ToolContext",
    "call_tool",
    "describe_validation_error",
    "is_error",
    "serialize",
    "tool_schemas",
]
