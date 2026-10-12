"""The `libraries` tool: this project's dependencies and what Grepwise has indexed."""

from pydantic import BaseModel, ConfigDict, Field

from grepwise.ecosystems import get_ecosystem
from grepwise.tools.context import ToolContext
from grepwise.tools.errors import returns_tool_errors
from grepwise.tools.workspace import (
    dependency_sources,
    embedding_progress,
    find_sources,
    load_project,
)

PROJECT_FIELD = Field(
    default=None,
    description="Absolute path of the user's working directory (the project). Pass it: "
    "library names mean the versions this project locks.",
)


class LibrariesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str | None = PROJECT_FIELD
    include_transitive: bool = Field(
        default=False, description="Also list dependencies pulled in by other packages."
    )


class Library(BaseModel):
    name: str
    ecosystem: str = Field(description="Which package registry it comes from, e.g. pypi")
    version: str | None
    direct: bool
    status: str = Field(
        description="ready | embedding N% (searchable, ranking still improving) | indexing | "
        "not indexed (indexed automatically when you search it) | unavailable: <why>"
    )
    sources: list[str] = Field(description="name@version refs to use with search and read")


class OtherSource(BaseModel):
    ref: str
    type: str
    status: str


class ProjectFolder(BaseModel):
    root: str
    ecosystem: str


class LibrariesOutput(BaseModel):
    project: str | None = Field(description="The project folder, or null if none was found")
    projects: list[ProjectFolder] = Field(
        description="Every project found from there, e.g. a backend/ and a ui/ folder"
    )
    dependencies: list[Library]
    transitive_not_listed: int
    other_sources: list[OtherSource] = Field(
        description="Sources added by hand (repos, folders, API specs) that aren't packages"
    )


def _status(ctx: ToolContext, source_ids: list[int], statuses: list[str]) -> str:
    if "indexing" in statuses:
        return "indexing"
    done = total = 0
    for sid in source_ids:
        d, t = embedding_progress(ctx.conn, sid, ctx.embedder.model_id)
        done, total = done + d, total + t
    if total and done < total:
        return f"embedding {100 * done // total}%"
    return "ready"


@returns_tool_errors
def libraries(ctx: ToolContext, inp: LibrariesInput) -> LibrariesOutput:
    project = load_project(ctx, inp.project)
    listed: list[Library] = []
    hidden = 0
    claimed: set[int] = set()
    for dep in project.dependencies if project else []:
        found = dependency_sources(ctx.conn, dep) if dep.version else []
        claimed |= {s.id for s in found}
        if not (inp.include_transitive or dep.direct):
            hidden += 1
            continue
        if found:
            status = _status(ctx, [s.id for s in found], [s.status for s in found])
        elif not dep.from_registry:
            registry = get_ecosystem(dep.ecosystem).registry
            status = f"unavailable: not from {registry} (add it by URL or folder)"
        elif dep.version is None:
            status = "unavailable: no locked or installed version"
        else:
            status = "not indexed"
        listed.append(Library(name=dep.name, ecosystem=dep.ecosystem, version=dep.version,
                              direct=dep.direct, status=status,
                              sources=[s.ref for s in found]))  # fmt: skip
    others = [
        OtherSource(ref=s.ref, type=s.type, status=_status(ctx, [s.id], [s.status]))
        for s in find_sources(ctx.conn, "package IS NULL", ())
        if s.id not in claimed
    ]
    return LibrariesOutput(
        project=str(project.root) if project else None,
        projects=[
            ProjectFolder(root=str(r.path), ecosystem=r.ecosystem)
            for r in (project.roots if project else [])
        ],
        dependencies=listed,
        transitive_not_listed=hidden,
        other_sources=others,
    )
