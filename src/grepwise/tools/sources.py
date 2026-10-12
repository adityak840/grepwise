"""Source references ('name' or 'name@version') and the list_sources tool."""

import difflib
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from grepwise.db.connection import json_list
from grepwise.search import citations
from grepwise.tools.context import ToolContext
from grepwise.tools.errors import ToolFailure, returns_tool_errors

SourceType = Literal["repo", "docs", "openapi"]


def split_ref(ref: str) -> tuple[str, str | None]:
    name, version = citations.split_ref(ref)
    return name, version or None


def _known(ctx: ToolContext, types: Sequence[str] | None) -> list[tuple[str, str]]:
    rows = ctx.conn.execute(
        "SELECT DISTINCT name, version FROM sources "
        "WHERE ?1 IS NULL OR type IN (SELECT value FROM json_each(?1)) ORDER BY 1, 2",
        (json_list(types) if types else None,),
    ).fetchall()
    return [(str(n), str(v)) for n, v in rows]


def _not_found(ref: str, known: list[tuple[str, str]], what: str = "source") -> ToolFailure:
    names = sorted({n for n, _ in known})
    name, version = split_ref(ref)
    if version and name in names:
        versions = ", ".join(f"{n}@{v}" for n, v in known if n == name)
        return ToolFailure(
            "source_not_found",
            f"{name} is not indexed at version {version}.",
            f"Indexed versions: {versions}.",
        )
    close = difflib.get_close_matches(name, names, n=3)
    hint = f"Did you mean {', '.join(close)}? " if close else ""
    available = ", ".join(names) if names else "nothing is indexed yet"
    return ToolFailure(
        "source_not_found",
        f"No {what} named {name!r}.",
        f"{hint}Available: {available}. Call list_sources to see every source and version.",
    )


def check_refs(
    ctx: ToolContext, refs: Sequence[str] | None, types: Sequence[str] | None = None
) -> None:
    """For search and find_symbol: every ref must match something ('name' = all versions)."""
    if not refs:
        return
    known = _known(ctx, types)
    for ref in refs:
        name, version = split_ref(ref)
        if not any(n == name and (version is None or v == version) for n, v in known):
            raise _not_found(ref, known)


def resolve_one(ctx: ToolContext, ref: str, types: Sequence[str] | None = None) -> tuple[str, str]:
    """For read_file and get_endpoint: a ref must mean exactly one (name, version)."""
    known = _known(ctx, types)
    name, version = split_ref(ref)
    matches = [(n, v) for n, v in known if n == name and (version is None or v == version)]
    if not matches:
        raise _not_found(ref, known)
    if len(matches) > 1:
        refs = ", ".join(f"{n}@{v}" for n, v in matches)
        raise ToolFailure(
            "ambiguous_source",
            f"{name} is indexed at {len(matches)} versions.",
            f"Pass one of: {refs}.",
        )
    return matches[0]


# --- list_sources ------------------------------------------------------------------------


class ListSourcesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: SourceType | None = Field(
        default=None, description="Only list sources of this type: repo, docs or openapi."
    )


class SourceInfo(BaseModel):
    ref: str = Field(description="name@version, usable wherever a source is asked for")
    type: SourceType
    status: str
    documents: int
    chunks: int
    subpath: str | None
    indexed_at: str
    embedding_model: str | None


class ListSourcesOutput(BaseModel):
    sources: list[SourceInfo]
    truncated: bool = False


@returns_tool_errors
def list_sources(ctx: ToolContext, inp: ListSourcesInput) -> ListSourcesOutput:
    rows = ctx.conn.execute(
        """
        SELECT s.name, s.version, s.type, s.status, s.subpath, s.updated_at,
               (SELECT count(*) FROM documents d WHERE d.source_id = s.id),
               (SELECT count(*) FROM chunks c WHERE c.source_id = s.id),
               s.embedding_model
        FROM sources s
        WHERE ?1 IS NULL OR s.type = ?1
        ORDER BY s.name, s.version, s.type
        """,
        (inp.type,),
    ).fetchall()
    return ListSourcesOutput(
        sources=[
            SourceInfo(
                ref=f"{name}@{version}",
                type=type_,
                status=status,
                documents=docs,
                chunks=chunks,
                subpath=subpath,
                indexed_at=updated,
                embedding_model=model,
            )
            for name, version, type_, status, subpath, updated, docs, chunks, model in rows
        ]
    )
