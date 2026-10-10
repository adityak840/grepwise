"""What the MCP tools know about the user's project: which library a plain name means, which
sources to search by default, and indexing a dependency on demand (spec 13, sections 1-3)."""

import difflib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from grepwise.db.connection import Conn
from grepwise.ingest.pipeline import index_repo
from grepwise.project import Dependency, Project, find_root, normalize_name, read_project
from grepwise.resolve import Resolution, Resolver
from grepwise.tools.context import ToolContext

log = logging.getLogger(__name__)

STALE_INDEXING = "-10 minutes"  # an 'indexing' source older than this was abandoned


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
    root = find_root(start) if start.exists() else None
    return read_project(root) if root else None


def find_sources(conn: Conn, where: str, params: tuple[object, ...]) -> list[Source]:
    rows = conn.execute(
        f"SELECT id, name, version, type, status FROM sources WHERE {where} ORDER BY name, version",
        params,
    ).fetchall()
    return [Source(*row) for row in rows]


def dependency_sources(conn: Conn, dep: Dependency) -> list[Source]:
    """Sources holding this dependency at its locked version: added by package, or (via a cached
    resolution) indexed by its repo URL at the same tag, like `fastapi/typer@0.27.2`."""
    found = find_sources(conn, "package = ? AND version = ?", (dep.name, dep.version))
    if found:
        return found
    row = conn.execute(
        "SELECT repo_url, tag FROM resolutions "
        "WHERE ecosystem = 'pypi' AND package = ? AND version = ?",
        (dep.name, dep.version),
    ).fetchone()
    if row is None:
        return []
    return find_sources(conn, "url = ? AND coalesce(ref, version) = ?", (row[0], row[1]))


def default_sources(conn: Conn, project: Project | None) -> list[Source]:
    """With a project: its dependencies at their locked versions, plus everything added by
    hand that isn't a package. Without one: everything."""
    if project is None:
        return find_sources(conn, "1", ())
    found = {s.id: s for s in find_sources(conn, "package IS NULL", ())}
    for dep in project.dependencies:
        if dep.version:
            found.update({s.id: s for s in dependency_sources(conn, dep)})
    return sorted(found.values(), key=lambda s: (s.name, s.version))


def lookup(
    ctx: ToolContext, project: Project | None, names: list[str], *, index_missing: bool = True
) -> Lookup:
    """Sources for each name: 'typer' (this project's version), 'typer@0.27.2', or any indexed
    source name. A dependency that isn't indexed yet is indexed on the spot (chunks now,
    vectors in the background); anything else gets a note instead of an error."""
    out = Lookup()
    for raw in names:
        name, _, version = raw.strip().partition("@")
        if version:
            found = find_sources(ctx.conn, "(name = ? OR package = ?) AND version = ?",
                             (name, normalize_name(name), version))  # fmt: skip
            if found:
                out.sources += found
            else:
                out.notes.append(
                    f"{raw} isn't indexed. The user can run `grepwise add {name}=={version}`."
                )
            continue
        dep = project.get(name) if project else None
        if dep is not None and dep.version:
            found = dependency_sources(ctx.conn, dep)
            if found and all(s.status == "indexing" for s in found):
                out.notes.append(
                    f"{dep.name} {dep.version} is being indexed by another session: "
                    "try again shortly."
                )
                continue
            if not found and index_missing:
                found = _index_dependency(ctx, dep, out.notes)
            if found:
                out.sources += found
                continue
        by_name = find_sources(ctx.conn, "name = ? OR package = ? OR lower(name) = lower(?)",
                           (name, normalize_name(name), name))  # fmt: skip
        if by_name:
            out.sources += by_name
        elif dep is None:
            where = "this project's dependencies or " if project else ""
            known = [r[0] for r in ctx.conn.execute("SELECT DISTINCT name FROM sources")]
            known += [d.name for d in project.dependencies] if project else []
            close = difflib.get_close_matches(name, known, n=3)
            hint = f" Did you mean {', '.join(close)}?" if close else ""
            out.notes.append(
                f"{name!r} isn't in {where}the index.{hint} "
                f"To add it, the user can run `grepwise add {name}`."
            )
    unique = {s.id: s for s in out.sources}
    out.sources = list(unique.values())
    return out


def _index_dependency(ctx: ToolContext, dep: Dependency, notes: list[str]) -> list[Source]:
    """Index a dependency now, without embeddings, and queue its vectors for the worker."""
    assert dep.version is not None
    if not dep.from_pypi:
        notes.append(
            f"{dep.name} isn't from PyPI (a git or path dependency): add it by URL or folder."
        )
        return []
    busy = find_sources(
        ctx.conn,
        f"package = ? AND version = ? AND status = 'indexing' "
        f"AND updated_at > strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '{STALE_INDEXING}')",
        (dep.name, dep.version),
    )
    if busy:
        notes.append(
            f"{dep.name} {dep.version} is being indexed by another session: try again shortly."
        )
        return []
    try:
        res: Resolution = Resolver(ctx.conn).resolve(dep.name, dep.version)
        # Already indexed by its repo URL at this tag (e.g. `grepwise add <url> --ref`)?
        same = find_sources(ctx.conn, "url = ? AND coalesce(ref, version) = ?",
                            (res.repo_url, res.tag))  # fmt: skip
        if same:
            return same
        summary = index_repo(
            ctx.conn, ctx.embedder, res.repo_url, res.tag, name=dep.name, version=dep.version,
            package=dep.name, embed=False,
        )  # fmt: skip
    except Exception as exc:
        log.info("On-demand indexing of %s failed: %s", dep.name, exc)
        notes.append(f"Couldn't index {dep.name} {dep.version}: {exc}")
        return []
    found = find_sources(ctx.conn, "package = ? AND version = ?", (dep.name, dep.version))
    for s in found:
        if ctx.worker is not None:
            ctx.worker.enqueue(s.id)
    pending = summary.embeddings_pending
    notes.append(
        f"{dep.name} {dep.version} was just indexed ({summary.chunks_by_kind.total()} chunks)."
        + (
            f" Semantic search is still filling in ({pending} chunks to embed): results come "
            "from keyword search until then, so search again later for better ranking."
            if pending
            else ""
        )
    )
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
