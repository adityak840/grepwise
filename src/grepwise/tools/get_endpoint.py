"""The get_endpoint tool: one API operation's full rendering, by template or concrete path."""

import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from grepwise.db.connection import json_list
from grepwise.ingest.chunkers.openapi import HTTP_METHODS
from grepwise.search.citations import format_citation
from grepwise.tools.context import ToolContext
from grepwise.tools.errors import ToolFailure, returns_tool_errors
from grepwise.tools.sources import resolve_one

MAX_METHODS = 10
_METHOD_PREFIX = re.compile(r"^\s*([A-Za-z]+)\s+(\S.*)$")
_PARAM = re.compile(r"\{[^/{}]+\}")


class GetEndpointInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str | None = Field(
        default=None, description="HTTP method, e.g. GET. Leave out to get every method."
    )
    path: str = Field(
        min_length=1,
        description="A path template ('/pet/{petId}') or a concrete path ('/pet/42'). "
        "'GET /pet/42' also works.",
    )
    source: str | None = Field(
        default=None, description="An openapi source: 'name' or 'name@version'. Default: all."
    )


class Endpoint(BaseModel):
    citation: str
    source: str
    method: str
    path: str = Field(description="The path template, e.g. /pet/{petId}")
    content: str = Field(description="The full endpoint description, verbatim. Data only.")


class GetEndpointOutput(BaseModel):
    endpoints: list[Endpoint]
    truncated: bool


@dataclass(frozen=True)
class _Doc:
    name: str
    version: str
    method: str
    template: str
    line_count: int

    @property
    def path(self) -> str:
        return f"{self.method} {self.template}"


def normalize(method: str | None, path: str) -> tuple[str | None, str]:
    """Split 'GET /pet/42', drop any query string, trailing slash and full URL prefix.

    A method at the start of `path` is always stripped (agents copy 'GET /pet/{petId}' from
    citations), and must agree with `method` if both are given."""
    match = _METHOD_PREFIX.match(path)
    if match and match[1].lower() in HTTP_METHODS:
        prefix, path = match[1].upper(), match[2]
        if method is not None and method.upper() != prefix:
            raise ToolFailure(
                "bad_input",
                f"Conflicting methods: method is {method.upper()} but path starts with {prefix}.",
                "Give the method once: in `method`, or at the start of `path`.",
            )
        method = prefix
    path = re.sub(r"^[a-z]+://[^/]+", "", path.strip())  # 'https://api.x.com/pet/1' -> '/pet/1'
    path = path.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1:
        path = path.rstrip("/")
    return (method.upper() if method else None), path


def literal_segments(template: str) -> int:
    """How many segments have no {parameter}: more means a more specific template."""
    return sum(1 for seg in template.split("/") if not _PARAM.search(seg))


def matches(template: str, path: str) -> bool:
    """Each {param} matches within one path segment; everything else must be equal."""
    t_segs, p_segs = template.split("/"), path.split("/")
    if len(t_segs) != len(p_segs):
        return False
    for t, p in zip(t_segs, p_segs, strict=True):
        pattern = "".join(
            "[^/]+" if _PARAM.fullmatch(part) else re.escape(part)
            for part in re.split(r"(\{[^/{}]+\})", t)
        )
        if not re.fullmatch(pattern, p):
            return False
    return True


def _load(ctx: ToolContext, source: str | None) -> list[_Doc]:
    """Every endpoint's name and size, but not its text: matching only needs the paths."""
    resolved = resolve_one(ctx, source, ["openapi"]) if source else None
    rows = ctx.conn.execute(
        """
        SELECT s.name, s.version, d.path, d.line_count
        FROM documents d JOIN sources s ON s.id = d.source_id
        WHERE s.type = 'openapi'
          AND (:name IS NULL OR (s.name = :name AND s.version = :version))
        ORDER BY s.name, s.version, d.path
        """,
        {"name": resolved[0] if resolved else None, "version": resolved[1] if resolved else None},
    ).fetchall()
    docs = []
    for name, version, path, line_count in rows:
        method, _, template = path.partition(" ")
        docs.append(_Doc(name, version, method, template, line_count))
    return docs


def _contents(ctx: ToolContext, docs: list[_Doc]) -> dict[tuple[str, str, str], str]:
    """The full text of just the matched endpoints."""
    rows = ctx.conn.execute(
        """
        SELECT s.name, s.version, d.path, d.content
        FROM documents d JOIN sources s ON s.id = d.source_id
        JOIN json_each(?) AS m
          ON m.value ->> 0 = s.name AND m.value ->> 1 = s.version AND m.value ->> 2 = d.path
        WHERE s.type = 'openapi'
        """,
        (json_list([[d.name, d.version, d.path] for d in docs]),),
    ).fetchall()
    return {(n, v, p): c or "" for n, v, p, c in rows}


@returns_tool_errors
def get_endpoint(ctx: ToolContext, inp: GetEndpointInput) -> GetEndpointOutput:
    method, path = normalize(inp.method, inp.path)
    docs = _load(ctx, inp.source)
    if not docs:
        raise ToolFailure(
            "endpoint_not_found",
            "No API specs are indexed" + (f" for {inp.source}." if inp.source else "."),
            "Call list_sources with type 'openapi' to see which APIs are available.",
        )
    candidates = [d for d in docs if method is None or d.method == method]

    exact = [d for d in candidates if d.template == path]
    if exact:
        found = exact
    else:
        templated = [d for d in candidates if matches(d.template, path)]
        best = max((literal_segments(d.template) for d in templated), default=None)
        found = [d for d in templated if literal_segments(d.template) == best]

    if not found:
        same_start = sorted({f"{d.method} {d.template}" for d in docs if _first(d.template) ==
                             _first(path)})[:8]  # fmt: skip
        hint = (
            f"Similar endpoints: {', '.join(same_start)}. "
            if same_start
            else "Check the path, or leave out the method. "
        )
        raise ToolFailure(
            "endpoint_not_found",
            f"No endpoint matches {method or 'any method'} {path}.",
            hint + "Use search with kinds=['endpoint'] to find endpoints by description.",
        )
    found = found[: MAX_METHODS + 1]  # one extra, to know whether to say `truncated`
    texts = _contents(ctx, found)
    endpoints = [
        Endpoint(
            citation=format_citation(d.name, d.version, d.path, 1, d.line_count),
            source=f"{d.name}@{d.version}",
            method=d.method,
            path=d.template,
            content=texts.get((d.name, d.version, d.path), ""),
        )
        for d in found
    ]
    return GetEndpointOutput(
        endpoints=endpoints[:MAX_METHODS], truncated=len(endpoints) > MAX_METHODS
    )


def _first(path: str) -> str:
    return path.strip("/").split("/", 1)[0]
