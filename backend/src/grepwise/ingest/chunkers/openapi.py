"""OpenAPI splitter: one virtual document and one `endpoint` chunk per operation (spec 02, 4).

Each operation (one method on one path) is rendered as plain text that stands alone:
`$ref`s are resolved inline up to MAX_REF_DEPTH levels; deeper or circular references
are shown as `→ Name`. Renderings are capped at MAX_CHUNK_CHARS.
"""

import contextlib
from dataclasses import dataclass
from typing import Any

from grepwise.ingest.chunkers import MAX_CHUNK_CHARS, Chunk, make_chunk, split_lines

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
MAX_REF_DEPTH = 3
TRUNCATED = "… (truncated)"
_MAX_REF_HOPS = 20  # for plain alias chains like A -> B -> C (not schema nesting)


@dataclass(frozen=True)
class Endpoint:
    path: str  # virtual document path, e.g. 'GET /pet/{petId}'
    content: str


class _Full(Exception):
    """Raised once a rendering is far enough past the cap that more lines can't matter."""


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _one_line(text: Any) -> str:
    return " ".join(str(text).split())


class _Renderer:
    def __init__(self, spec: dict[str, Any]):
        self.spec = spec
        self.lines: list[str] = []
        self.size = 0

    # --- references ---

    def lookup(self, ref: str) -> Any:
        """Resolve a local JSON pointer like '#/components/schemas/Pet'; None if not found."""
        if not ref.startswith("#/"):
            return None  # external refs are out of scope
        node: Any = self.spec
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def deref(self, obj: Any) -> Any:
        """Follow $refs for parameters, request bodies and responses (not schemas)."""
        for _ in range(_MAX_REF_HOPS):
            if not (isinstance(obj, dict) and "$ref" in obj):
                return obj
            obj = self.lookup(str(obj["$ref"]))
        return None

    # --- output ---

    def emit(self, line: str) -> None:
        self.lines.append(line)
        self.size += len(line) + 1
        if self.size > 2 * MAX_CHUNK_CHARS:
            raise _Full

    # --- schemas ---

    def label(self, schema: Any) -> str:
        """Short type label: 'integer<int64>', 'array of Pet', 'string enum: a, b'."""
        if not isinstance(schema, dict):
            return "any"
        if "$ref" in schema:
            return _ref_name(str(schema["$ref"]))
        for combo, word in (("oneOf", "one of"), ("anyOf", "any of"), ("allOf", "all of")):
            if isinstance(schema.get(combo), list):
                return f"{word} " + " | ".join(self.label(s) for s in schema[combo])
        type_ = schema.get("type")
        if isinstance(type_, list):  # OpenAPI 3.1: type: [string, "null"]
            type_ = " | ".join(str(t) for t in type_)
        if type_ == "array" or "items" in schema:
            return f"array of {self.label(schema.get('items'))}"
        text = str(type_ or ("object" if "properties" in schema else "any"))
        if schema.get("format"):
            text += f"<{schema['format']}>"
        if isinstance(schema.get("enum"), list):
            text += " enum: " + ", ".join(str(v) for v in schema["enum"])
        return text

    def schema(self, schema: Any, indent: int, depth: int = 0, chain: tuple[str, ...] = ()) -> None:
        """Emit the inside of `schema` (its properties), one line per property."""
        if not isinstance(schema, dict):
            return
        pad = "  " * indent
        if "$ref" in schema:
            ref = str(schema["$ref"])
            target = self.lookup(ref)
            if ref in chain or depth >= MAX_REF_DEPTH or not isinstance(target, dict):
                self.emit(f"{pad}→ {_ref_name(ref)}")
                return
            self.schema(target, indent, depth + 1, (*chain, ref))
            return
        for part in schema.get("allOf") or []:
            self.schema(part, indent, depth, chain)
        for combo in ("oneOf", "anyOf"):
            for option in schema.get(combo) or []:
                self.emit(f"{pad}option {self.label(option)}:")
                self.schema(option, indent + 1, depth, chain)
        if "items" in schema:
            self.schema(schema["items"], indent, depth, chain)
        required = set(schema.get("required") or [])
        properties = schema.get("properties")
        for name, prop in properties.items() if isinstance(properties, dict) else []:
            details = self.label(prop) + (", required" if name in required else "")
            desc = _one_line(prop.get("description", "")) if isinstance(prop, dict) else ""
            self.emit(f"{pad}{name} ({details})" + (f": {desc}" if desc else ""))
            self.schema(prop, indent + 1, depth, chain)
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            self.emit(f"{pad}additional properties ({self.label(extra)})")
            self.schema(extra, indent + 1, depth, chain)

    def media(self, content: Any) -> tuple[str, Any] | None:
        """Pick one media type to show, preferring JSON."""
        if not isinstance(content, dict) or not content:
            return None
        media_type = "application/json" if "application/json" in content else next(iter(content))
        return media_type, (content[media_type] or {}).get("schema")

    def schema_block(self, schema: Any, indent: int) -> None:
        if schema is None:
            return
        self.emit("  " * indent + self.label(schema))
        self.schema(schema, indent + 1)

    # --- operations ---

    def operation(self, method: str, path: str, op: dict[str, Any], shared_params: Any) -> None:
        self.emit(f"{method.upper()} {path}")
        for key, label in (("operationId", "Operation"), ("summary", "Summary")):
            if op.get(key):
                self.emit(f"{label}: {_one_line(op[key])}")
        if op.get("tags"):
            self.emit("Tags: " + ", ".join(str(t) for t in op["tags"]))
        if op.get("deprecated"):
            self.emit("Deprecated: yes")

        if op.get("description"):
            self.emit("")
            self.emit("Description:")
            for line in str(op["description"]).strip().splitlines():
                self.emit(line.rstrip())

        params = self.parameters(shared_params, op.get("parameters"))
        if params:
            self.emit("")
            self.emit("Parameters:")
            for p in params:
                details = [str(p.get("in", "?"))]
                if p.get("required"):
                    details.append("required")
                details.append(self.label(p.get("schema")))
                desc = _one_line(p.get("description", ""))
                line = f"- {p.get('name', '?')} ({', '.join(details)})"
                self.emit(line + (f": {desc}" if desc else ""))

        body = self.deref(op.get("requestBody"))
        picked = self.media(body.get("content")) if isinstance(body, dict) else None
        if picked:
            self.emit("")
            self.emit(f"Request body ({picked[0]}):")
            self.schema_block(picked[1], 1)

        raw_responses = op.get("responses")
        responses = {
            status: resolved
            for status, resp in (raw_responses.items() if isinstance(raw_responses, dict) else [])
            if isinstance(resolved := self.deref(resp), dict)
        }
        if responses:
            self.emit("")
            self.emit("Responses:")
            for status, resp in responses.items():
                desc = _one_line(resp.get("description", ""))
                self.emit(f"- {status}" + (f": {desc}" if desc else ""))
                picked = self.media(resp.get("content"))
                if picked:
                    self.schema_block(picked[1], 1)

    def parameters(self, shared: Any, own: Any) -> list[dict[str, Any]]:
        """Path-level parameters, overridden by operation-level ones with the same name+in."""
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for raw in [*(shared or []), *(own or [])]:
            p = self.deref(raw)
            if isinstance(p, dict):
                merged[(str(p.get("name")), str(p.get("in")))] = p
        return list(merged.values())


def _cap(lines: list[str], max_chars: int = MAX_CHUNK_CHARS) -> str:
    text = "\n".join(lines) + "\n"
    if len(text) <= max_chars:
        return text
    budget = max_chars - len(TRUNCATED) - 1
    kept: list[str] = []
    for line in lines:
        budget -= len(line) + 1
        if budget < 0:
            break
        kept.append(line)
    return "\n".join([*kept, TRUNCATED]) + "\n"


def render_operations(spec: dict[str, Any]) -> list[Endpoint]:
    endpoints = []
    for path, item in spec.get("paths", {}).items():
        item = _Renderer(spec).deref(item)
        if not isinstance(item, dict):
            continue
        for method in HTTP_METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            r = _Renderer(spec)
            # _Full means we're already well past the cap; _cap trims and marks it truncated.
            with contextlib.suppress(_Full):
                r.operation(method, str(path), op, item.get("parameters"))
            endpoints.append(Endpoint(f"{method.upper()} {path}", _cap(r.lines)))
    return endpoints


def chunk_endpoint(endpoint: Endpoint) -> Chunk:
    """The whole rendering as one chunk, lines 1..n."""
    lines = split_lines(endpoint.content)
    return make_chunk(lines, "endpoint", endpoint.path, 1, len(lines))
