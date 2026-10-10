"""`grepwise add`, `deps`, `sync` and `remove`: managing what's indexed (spec 12, section 6)."""

import re
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from grepwise.config import get_settings
from grepwise.db.connection import Conn, connect
from grepwise.ingest import store
from grepwise.ingest.embed import Embedder, make_embedder
from grepwise.ingest.pipeline import IndexSummary, index_local, index_repo
from grepwise.project import Dependency, Project, find_root, normalize_name, read_project
from grepwise.resolve import Resolver

console = Console()

_SPEC = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)(?:==([A-Za-z0-9._+!-]+))?")


def print_summary(s: IndexSummary) -> None:
    sha = f" ({s.commit_sha[:12]})" if s.commit_sha else ""
    console.print(f"[green]Indexed[/green] {s.name}@{s.version}{sha}")
    skipped = ", ".join(f"{n} {reason}" for reason, n in sorted(s.skipped.items())) or "none"
    kinds = ", ".join(f"{n} {kind}" for kind, n in sorted(s.chunks_by_kind.items())) or "none"
    console.print(f"  documents:  {s.files_kept} kept; skipped: {skipped}")
    console.print(f"  chunks:     {s.chunks_by_kind.total()} ({kinds})")
    console.print(f"  embeddings: {s.embeddings_created} created, {s.embeddings_reused} reused")
    console.print(f"  time:       {s.seconds:.1f}s")


def _fail(message: str) -> typer.Exit:
    console.print(f"[red]{message}[/red]", highlight=False)
    return typer.Exit(1)


def _project() -> Project:
    root = find_root(Path.cwd())
    if root is None:
        raise _fail(
            "No Python project here: no pyproject.toml, uv.lock, poetry.lock or "
            "requirements*.txt in this folder or above it."
        )
    return read_project(root)


def _is_path(target: str) -> bool:
    return target.startswith((".", "/", "~")) or (
        "/" in target and "://" not in target and Path(target).is_dir()
    )


def add_package(
    conn: Conn, embedder: Embedder, resolver: Resolver, package: str, version: str
) -> IndexSummary:
    res = resolver.resolve(package, version)
    cached = " (cached)" if res.cached else ""
    console.print(
        f"{res.package} {version}: {res.repo_url} at tag {res.tag}{cached}", highlight=False
    )
    return index_repo(
        conn,
        embedder,
        res.repo_url,
        res.tag,
        name=res.package,
        version=version,
        package=res.package,
    )


def add(
    target: Annotated[
        str,
        typer.Argument(
            help="A package (typer, typer==0.27.2), a GitHub repo URL, or a folder (./lib)"
        ),
    ],
    ref: Annotated[str | None, typer.Option("--ref", help="Tag or branch, for a repo URL")] = None,
    name: Annotated[str | None, typer.Option("--name", help="Source name (URL or folder)")] = None,
    version: Annotated[
        str | None, typer.Option("--version", help="Version label (URL or folder)")
    ] = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Look the package up again, ignoring the cache")
    ] = False,
) -> None:
    """Index a library: by package name (at your project's version), repo URL, or folder."""
    settings = get_settings()
    try:
        embedder = make_embedder(settings)
        with closing(connect()) as conn:
            if "://" in target:
                if not ref:
                    raise _fail("A repo URL needs --ref <tag or branch>.")
                summary = index_repo(conn, embedder, target, ref, name=name, version=version)
            elif _is_path(target):
                summary = index_local(conn, embedder, Path(target), name=name, version=version)
            else:
                spec = _SPEC.fullmatch(target.strip())
                if spec is None:
                    raise _fail(f"Not a package, URL or folder: {target!r}.")
                package, pinned = normalize_name(spec[1]), spec[2]
                resolver = Resolver(conn, refresh=refresh)
                chosen = pinned or _project_version(package) or resolver.latest_version(package)
                summary = add_package(conn, embedder, resolver, package, chosen)
    except typer.Exit:
        raise
    except Exception as exc:
        raise _fail(f"Couldn't add {target}: {exc}") from None
    print_summary(summary)


def _project_version(package: str) -> str | None:
    """The version locked in the current project, if there is one; says where it came from."""
    root = find_root(Path.cwd())
    dep = read_project(root).get(package) if root else None
    if dep and dep.version:
        console.print(f"Using {package} {dep.version} from {dep.version_from}", style="dim")
        return dep.version
    console.print(
        f"{package} isn't locked in a project here: using the latest release.", style="dim"
    )
    return None


def deps(
    all_: Annotated[bool, typer.Option("--all", help="Include transitive dependencies")] = False,
) -> None:
    """This project's dependencies, their exact versions, and whether they're indexed."""
    project = _project()
    with closing(connect()) as conn:
        indexed = store.indexed_packages(conn)
    shown = [d for d in project.dependencies if all_ or d.direct]
    table = Table("package", "version", "kind", "version from", "indexed", title=str(project.root))
    for d in shown:
        mark = "✓" if (d.name, d.version) in indexed else ""
        kind = ("direct" if d.direct else "transitive") + ("" if d.from_pypi else ", not PyPI")
        table.add_row(d.name, d.version or "-", kind, d.version_from or "-", mark)
    console.print(table)
    hidden = len(project.dependencies) - len(shown)
    if hidden:
        console.print(f"+ {hidden} transitive (--all to list them)", style="dim")


@dataclass
class _Outcome:
    dep: Dependency
    status: str  # added | indexed | would add | skipped | failed
    detail: str = ""


def sync(
    all_: Annotated[bool, typer.Option("--all", help="Transitive dependencies too")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Only work out repos and tags; index nothing")
    ] = False,
    refresh: Annotated[bool, typer.Option("--refresh", help="Ignore cached lookups")] = False,
) -> None:
    """Index every dependency of this project that isn't indexed at its locked version."""
    project = _project()
    targets = [d for d in project.dependencies if all_ or d.direct]
    outcomes: list[_Outcome] = []
    with closing(connect()) as conn:
        indexed = store.indexed_packages(conn)
        resolver = Resolver(conn, refresh=refresh)
        embedder: Embedder | None = None
        for d in targets:
            if not d.from_pypi or d.version is None:
                why = "not from PyPI" if not d.from_pypi else "no locked or installed version"
                outcomes.append(_Outcome(d, "skipped", why))
                continue
            if (d.name, d.version) in indexed:
                outcomes.append(_Outcome(d, "indexed"))
                continue
            try:
                if dry_run:
                    res = resolver.resolve(d.name, d.version)
                    outcomes.append(_Outcome(d, "would add", f"{res.repo_url} at {res.tag}"))
                    continue
                embedder = embedder or make_embedder(get_settings())
                s = add_package(conn, embedder, resolver, d.name, d.version)
                detail = f"{s.chunks_by_kind.total()} chunks, {s.seconds:.0f}s"
                outcomes.append(_Outcome(d, "added", detail))
            except Exception as exc:
                outcomes.append(_Outcome(d, "failed", str(exc)))
    table = Table("package", "version", "result", "detail")
    styles = {"failed": "red", "added": "green", "would add": "cyan", "skipped": "dim"}
    for o in outcomes:
        style = styles.get(o.status, "")
        table.add_row(
            o.dep.name,
            o.dep.version or "-",
            f"[{style}]{o.status}[/]" if style else o.status,
            o.detail,
        )
    console.print(table)
    if any(o.status == "failed" for o in outcomes):
        raise typer.Exit(1)


def remove(
    ref: Annotated[str, typer.Argument(help="name@version, as `grepwise sources` shows it")],
    type_: Annotated[
        str | None, typer.Option("--type", help="repo, docs or openapi, if several match")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation")] = False,
) -> None:
    """Delete an indexed source (its embeddings are kept, so re-adding it is quick)."""
    name, _, version = ref.rpartition("@")
    if not name or not version:
        raise _fail("Give the source as name@version, e.g. typer@0.27.2.")
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT s.id, s.type, (SELECT count(*) FROM chunks c WHERE c.source_id = s.id) "
            "FROM sources s WHERE s.name = ? AND s.version = ? AND (?3 IS NULL OR s.type = ?3)",
            (name, version, type_),
        ).fetchall()
        if not rows:
            raise _fail(f"No source {ref}" + (f" of type {type_}" if type_ else "") + ".")
        if len(rows) > 1:
            types = ", ".join(t for _, t, _ in rows)
            raise _fail(f"{ref} is indexed as {types}: pass --type to choose one.")
        source_id, kind, chunks = rows[0]
        if not yes and not typer.confirm(f"Delete {ref} ({kind}, {chunks} chunks)?"):
            raise typer.Exit(1)
        store.remove_source(conn, source_id)
    console.print(f"Removed {ref} ({kind}, {chunks} chunks).", highlight=False)
