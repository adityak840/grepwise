"""What the MCP tools know about the user's project: which library a plain name means, which
sources to search by default, and indexing a dependency on demand (spec 13, sections 1-3)."""

import difflib
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from grepwise.db.connection import Conn, json_list
from grepwise.ecosystems import Dependency, Project, ecosystems, get_ecosystem, parse_library
from grepwise.ecosystems import load_project as find_project
from grepwise.ingest.pipeline import index_repo
from grepwise.resolve import Resolution, ResolveError, Resolver
from grepwise.search.vectors import MIN_COVERAGE
from grepwise.tools.context import ToolContext

log = logging.getLogger(__name__)

STALE_INDEXING = "-10 minutes"  # an 'indexing' source older than this was abandoned

# A name matches a source's name, its package, or (for sources added by URL) the repo part of
# an owner/repo name, so 'typer' finds 'fastapi/typer' (how people say it), while 'node' doesn't
# find the npm package '@types/node'. Case-insensitive; no LIKE wildcards.
_NAME_MATCH = (
    "(name = :name OR package IN (SELECT value FROM json_each(:packages)) "
    "OR lower(name) = lower(:name) "
    "OR (package IS NULL AND lower(substr(name, instr(name, '/') + 1)) = lower(:name)))"
)


@dataclass(frozen=True)
class Source:
    id: int
    name: str
    version: str
    type: str
    status: str

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass
class Lookup:
    sources: list[Source] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def load_project(ctx: ToolContext, project: str | None) -> Project | None:
    start = Path(project).expanduser() if project else ctx.cwd
    return find_project(start) if start.exists() else None


def find_sources(
    conn: Conn, where: str, params: tuple[object, ...] | Mapping[str, object]
) -> list[Source]:
    rows = conn.execute(
        f"SELECT id, name, version, type, status FROM sources WHERE {where} ORDER BY name, version",
        params,
    ).fetchall()
    return [Source(*row) for row in rows]


def dependency_sources(conn: Conn, dep: Dependency) -> list[Source]:
    """Sources holding this dependency at its locked version: added by package, or (via a cached
    resolution) indexed by its repo URL at the same tag, like `fastapi/typer@0.27.2`."""
    found = find_sources(
        conn, "ecosystem = ? AND package = ? AND version = ?",
        (dep.ecosystem, dep.name, dep.version),
    )  # fmt: skip
    if found:
        return found
    row = conn.execute(
        "SELECT repo_url, tag FROM resolutions WHERE ecosystem = ? AND package = ? AND version = ?",
        (dep.ecosystem, dep.name, dep.version),
    ).fetchone()
    if row is None:
        return []
    return find_sources(conn, "url = ? AND coalesce(ref, version) = ?", (row[0], row[1]))


def default_sources(conn: Conn, project: Project | None) -> list[Source]:
    """With a project: its dependencies at their locked versions (other sources are reached by
    naming them, so a Typer project's search isn't mixed with unrelated libraries). Without
    one: everything."""
    if project is None:
        return find_sources(conn, "1", ())
    found: dict[int, Source] = {}
    for dep in project.dependencies:
        if dep.version:
            found.update({s.id: s for s in dependency_sources(conn, dep)})
    return sorted(found.values(), key=lambda s: (s.name, s.version))


def lookup(
    ctx: ToolContext, project: Project | None, names: list[str], *, index_missing: bool = True
) -> Lookup:
    """Sources for each name: 'typer' (this project's version, in any of its ecosystems),
    'typer@0.27.2', 'npm:recharts' (that ecosystem only), or any indexed source name. A
    dependency that isn't indexed yet is indexed on the spot (chunks now, vectors in the
    background); anything else gets a note instead of an error."""
    out = Lookup()
    for raw in names:
        eco, name, version = parse_library(raw)
        params = {"name": name, "packages": json_list(package_names(name, eco)),
                  "version": version, "eco": eco}  # fmt: skip
        in_eco = "(:eco IS NULL OR ecosystem IN (:eco, ''))"
        deps = [d for d in project.matching(name, eco) if d.version] if project else []
        if version and any(version in (d.version, f"v{d.version}") for d in deps):
            deps = [d for d in deps if version in (d.version, f"v{d.version}")]
            version = ""  # the project's own version, named: same as the plain name
        if version:
            # The version can also be the git tag ('typer@v0.27.2').
            where = f"{_NAME_MATCH} AND {in_eco} AND (version = :version OR ref = :version)"
            found = find_sources(ctx.conn, where, params)
            if found:
                out.sources += found
            else:
                out.notes.append(
                    f"{raw} isn't indexed. {add_hint(ctx, project, eco, name, version)}"
                )
            continue
        handled = False
        for dep in deps:  # a name in two ecosystems is searched in both: both are dependencies
            found = dependency_sources(ctx.conn, dep)
            if found and all(s.status == "indexing" for s in found):
                out.notes.append(
                    f"{dep.name} {dep.version} is being indexed by another session: "
                    "try again shortly."
                )
                handled = True
                continue
            if not found and index_missing:
                found = _index_dependency(ctx, dep, out.notes)
            out.sources += found
            handled = handled or bool(found)
        if handled:
            continue
        by_name = find_sources(ctx.conn, f"{_NAME_MATCH} AND {in_eco}", params)
        if by_name:
            out.sources += by_name
        elif not deps:
            where = "this project's dependencies or " if project else ""
            known = [r[0] for r in ctx.conn.execute("SELECT DISTINCT name FROM sources")]
            known += [d.name for d in project.dependencies] if project else []
            close = difflib.get_close_matches(name, known, n=3)
            hint = f" Did you mean {', '.join(close)}?" if close else ""
            out.notes.append(
                f"{name!r} isn't in {where}the index.{hint} "
                f"{add_hint(ctx, project, eco, name, version)}"
            )
    unique = {s.id: s for s in out.sources}
    out.sources = list(unique.values())
    return out


def package_names(name: str, eco: str | None) -> list[str]:
    """How `name` is stored as a package in each ecosystem it could belong to."""
    chosen = [get_ecosystem(eco)] if eco else ecosystems()
    return sorted({e.normalize(name) for e in chosen})


BY_URL = "`grepwise add https://github.com/<owner>/<repo> --ref <tag>`"


def add_hint(
    ctx: ToolContext, project: Project | None, eco: str | None, name: str, version: str = ""
) -> str:
    """How the user can add `name`. `grepwise add <name>` looks the name up in a registry, so
    it's only suggested for a registry that has it: the named ecosystem, else the project's,
    else PyPI and npm."""
    url = ctx.conn.execute(
        f"SELECT url FROM sources WHERE {_NAME_MATCH} AND url LIKE 'https://github.com/%' LIMIT 1",
        {"name": name, "packages": json_list(package_names(name, eco))},
    ).fetchone()
    if url and version:  # another version of a repo that's already indexed
        return f"The user can run `grepwise add {url[0]} --ref <tag for {version}>`."
    if "/" in name and not name.startswith("@"):  # owner/repo: not a package name
        return f"It isn't a package name: the user can run {BY_URL}."
    if eco:
        tried = [eco]
    elif project and project.roots:
        tried = list(dict.fromkeys(r.ecosystem for r in project.roots))
    else:
        tried = [e.name for e in ecosystems()]
    registries = " or ".join(get_ecosystem(e).registry for e in tried)
    for e in tried:
        try:
            if Resolver(ctx.conn, ecosystem=e).on_registry(name, version or None):
                return f"The user can run `grepwise add {add_spec(e, name, version)}`."
        except ResolveError:
            return (
                f"The user can run `grepwise add {add_spec(tried[0], name, version)}` if it's on "
                f"{registries}, or else {BY_URL}."
            )
    at = f" {version}" if version else ""
    return f"{registries} has no {name}{at}, so if it's on GitHub the user can run {BY_URL}."


def add_spec(eco: str, name: str, version: str = "") -> str:
    """What `grepwise add` takes: 'typer==0.27.2' for PyPI, 'npm:recharts@2.15.0' for npm."""
    if eco == "pypi":
        return f"{name}=={version}" if version else name
    return f"{eco}:{name}@{version}" if version else f"{eco}:{name}"


def _index_dependency(ctx: ToolContext, dep: Dependency, notes: list[str]) -> list[Source]:
    """Index a dependency now, without embeddings, and queue its vectors for the worker."""
    assert dep.version is not None
    registry = get_ecosystem(dep.ecosystem).registry
    if not dep.from_registry:
        notes.append(
            f"{dep.name} isn't from {registry} (a git or path dependency): add it by URL or folder."
        )
        return []
    same_package = "ecosystem = ? AND package = ? AND version = ?"
    key = (dep.ecosystem, dep.name, dep.version)
    busy = find_sources(
        ctx.conn,
        f"{same_package} AND status = 'indexing' "
        f"AND updated_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '{STALE_INDEXING}')",
        key,
    )
    if busy:
        notes.append(
            f"{dep.name} {dep.version} is being indexed by another session: try again shortly."
        )
        return []
    try:
        res: Resolution = Resolver(ctx.conn, ecosystem=dep.ecosystem).resolve(dep.name, dep.version)
        # Already indexed by its repo URL at this tag (e.g. `grepwise add <url> --ref`)?
        same = find_sources(ctx.conn, "url = ? AND coalesce(ref, version) = ?",
                            (res.repo_url, res.tag))  # fmt: skip
        if same:
            return same
        summary = index_repo(
            ctx.conn, ctx.embedder, res.repo_url, res.tag, name=dep.name, version=dep.version,
            package=dep.name, ecosystem=dep.ecosystem, subpath=res.subpath,
            locate=lambda root: get_ecosystem(dep.ecosystem).locate_package(root, dep.name),
            embed=False,
        )  # fmt: skip
    except Exception as exc:
        log.info("On-demand indexing of %s failed: %s", dep.name, exc)
        notes.append(f"Couldn't index {dep.name} {dep.version}: {exc}")
        return []
    found = find_sources(ctx.conn, same_package, key)
    for s in found:
        if ctx.worker is not None:
            ctx.worker.enqueue(s.id)
    notes.append(
        f"{dep.name} {dep.version} was just indexed ({summary.chunks_by_kind.total()} chunks)."
    )  # `search` adds the note about embeddings still filling in
    return found


def embedding_progress(conn: Conn, source_id: int, model_id: str) -> tuple[int, int]:
    """(chunks with a vector, all chunks) for a source."""
    row = conn.execute(
        """
        SELECT count(*), count(e.content_hash)
        FROM chunks c
        LEFT JOIN embeddings e ON e.content_hash = c.content_hash AND e.model_id = ?
        WHERE c.source_id = ?
        """,
        (model_id, source_id),
    ).fetchone()
    return (int(row[1]), int(row[0])) if row else (0, 0)


def still_embedding(ctx: ToolContext, sources: list[Source]) -> list[str]:
    """'docslib@2.0 (12%)' for each source under MIN_COVERAGE: while there are any, a search
    over them is keyword and symbol matches only (see search/vectors.py)."""
    out = []
    for s in sources:
        done, total = embedding_progress(ctx.conn, s.id, ctx.embedder.model_id)
        if total and done / total < MIN_COVERAGE:
            out.append(f"{s.ref} ({done * 100 // total}%)")
    return out
