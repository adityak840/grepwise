"""The tool layer: five read-only tools, served by the MCP server and the CLI.

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
            "list_sources",
            "List the indexed sources (code repos, docs, API specs) and their versions. "
            "Call this first if you don't know what is available.",
            ListSourcesInput,
            list_sources,
        ),
        Tool(
            "search",
            "Search code, docs and API specs by meaning and keywords. Use this for questions "
            "('how is the token refreshed?') or when you don't know an exact name. Returns "
            "short ranked snippets with citations.",
            SearchInput,
            search,
        ),
        Tool(
            "read_file",
            "Read exact lines of a file (or an API endpoint) from an indexed source. Use it to "
            "see more context around a search result: pass the source and path from its "
            "citation and a line range.",
            ReadFileInput,
            read_file,
        ),
        Tool(
            "find_symbol",
            "Find where a function, class, method or docs section is defined, by exact name. "
            "Use this when you know the name (e.g. 'Signer.sign' or 'sign'); use search for "
            "questions.",
            FindSymbolInput,
            find_symbol,
        ),
        Tool(
            "get_endpoint",
            "Get the full description of an API endpoint: parameters, request body and "
            "responses. Accepts a template ('/pet/{petId}') or a real path ('/pet/42').",
            GetEndpointInput,
            get_endpoint,
        ),
    ]
}


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


def tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": _clean_schema(t.input_model.model_json_schema()),
        }
        for t in TOOLS.values()
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
    "TOOLS",
    "Tool",
    "ToolContext",
    "call_tool",
    "describe_validation_error",
    "is_error",
    "serialize",
    "tool_schemas",
]
