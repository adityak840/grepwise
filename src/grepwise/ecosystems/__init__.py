"""Package ecosystems (spec 16): how a project declares its dependencies, where their exact
versions come from, and how a package maps to a GitHub repo.

An ecosystem is a language's package world: its manifest (pyproject.toml), lockfile (uv.lock)
and registry (PyPI). Everything after "which repo, at which tag" is shared. Python is the only
ecosystem so far; JS/TS (npm), Go and Rust plug in the same way.

A folder can hold several projects (a Python `backend/` next to a JS `ui/`), so the project is
found by walking up from the starting folder to the nearest one with any ecosystem's files, and
by also looking one level down.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grepwise.ingest.filters import SKIP_DIRS

HttpGet = Callable[[str], dict[str, Any] | None]  # parsed JSON, or None for a 404

_GITHUB = re.compile(
    r"github\.com[/:]([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?(?:[/#?]|$)", re.I
)
# github.com/<these>/... are GitHub pages, not repos (e.g. a "Funding" link to /sponsors/x).
_NOT_OWNERS = {"sponsors", "orgs", "apps", "marketplace", "features", "topics", "about", "site"}
MAX_SUBFOLDERS = 20  # looked at one level down from the starting folder


@dataclass(frozen=True)
class Dependency:
    name: str  # normalized for its ecosystem
    version: str | None  # None if no lockfile, pin or install has it
    direct: bool  # declared by the project (vs. only pulled in by another package)
    version_from: str | None  # e.g. 'uv.lock', 'requirements.txt', '.venv'
    from_registry: bool = True  # False for git or path sources in a lockfile
    ecosystem: str = "pypi"


@dataclass(frozen=True)
class RegistryInfo:
    repo: str | None  # https://github.com/<owner>/<repo>, if the registry links one
    subpath: str | None = None  # the package's folder, in a repo holding several packages


class Ecosystem:
    """What each ecosystem provides. Subclasses override every method."""

    name: str  # stored in the database: 'pypi'
    registry: str  # for messages: 'PyPI'

    def is_root(self, folder: Path) -> bool:
        raise NotImplementedError

    def read(self, root: Path) -> tuple[str | None, list[Dependency]]:
        """(the project's own package name, its dependencies)."""
        raise NotImplementedError

    def normalize(self, name: str) -> str:
        raise NotImplementedError

    def registry_info(self, name: str, version: str, http_get: HttpGet) -> RegistryInfo | None:
        """The repo for this release, or None if the registry has no such package/version."""
        raise NotImplementedError

    def latest_version(self, name: str, http_get: HttpGet) -> str | None:
        raise NotImplementedError

    def tag_candidates(self, name: str, version: str, pattern: str | None = None) -> list[str]:
        return tag_candidates(name, version, pattern)

    def unsupported(self, name: str) -> str | None:
        """Why this package can't be indexed by name (e.g. npm's @types/*), or None."""
        return None

    def locate_package(self, root: Path, name: str) -> str | None:
        """The package's folder in a cloned repo holding several, when the registry didn't
        say; None for the repo root."""
        return None


@dataclass(frozen=True)
class ProjectRoot:
    path: Path
    ecosystem: str
    name: str | None  # the project's own package, which is not a dependency


@dataclass(frozen=True)
class Project:
    root: Path  # the nearest project folder at or above the starting folder, else the start
    roots: list[ProjectRoot]
    dependencies: list[Dependency]

    def matching(self, name: str, ecosystem: str | None = None) -> list[Dependency]:
        """Dependencies called `name` (one per ecosystem that has it), or only in `ecosystem`."""
        wanted = {e.name: e.normalize(name) for e in ecosystems()}
        return [
            d for d in self.dependencies
            if ecosystem in (None, d.ecosystem) and d.name == wanted.get(d.ecosystem)
        ]  # fmt: skip

    def get(self, name: str, ecosystem: str | None = None) -> Dependency | None:
        found = self.matching(name, ecosystem)
        return found[0] if found else None


def _all() -> dict[str, Ecosystem]:
    from grepwise.ecosystems.npm import NPM
    from grepwise.ecosystems.pypi import PYPI

    return {e.name: e for e in (PYPI, NPM)}


def ecosystems() -> list[Ecosystem]:
    return list(_all().values())


def get_ecosystem(name: str) -> Ecosystem:
    found = _all().get(name)
    if found is None:
        raise ValueError(f"Unknown ecosystem {name!r}.")
    return found


def find_roots(start: Path) -> list[tuple[Path, Ecosystem]]:
    """Project folders for `start`: the nearest folder at or above it with any ecosystem's
    files (stopping below the home folder, so a stray ~/pyproject.toml never claims every
    project), plus folders one level down for ecosystems not found that way, when `start` is
    a project or a git repo root. So a repo root finds `backend/` and `ui/`, and a Python
    project with a `ui/` folder finds both, without a Python `examples/` folder adding its
    requirements, and without a plain folder of repos (~/Documents) merging them all."""
    start = start.resolve()
    home = Path.home().resolve()
    found: list[tuple[Path, Ecosystem]] = []
    for folder in [start, *start.parents]:
        if folder == home or folder == folder.parent:
            break
        here = [e for e in ecosystems() if e.is_root(folder)]
        if here:
            found = [(folder, e) for e in here]
            break
    at_start = any(folder == start for folder, _ in found)
    if start != home and (at_start or (start / ".git").exists()):
        taken = {e.name for _, e in found}
        try:
            subs = sorted(
                p for p in start.iterdir()
                if p.is_dir() and not p.name.startswith(".") and p.name not in SKIP_DIRS
            )  # fmt: skip
        except OSError:  # a folder this process can't list
            subs = []
        for sub in subs[:MAX_SUBFOLDERS]:
            found += [(sub, e) for e in ecosystems() if e.name not in taken and e.is_root(sub)]
    return found


def load_project(start: Path) -> Project | None:
    """Every project found from `start` (see find_roots), with their dependencies merged."""
    roots = find_roots(start)
    if not roots:
        return None
    project_roots: list[ProjectRoot] = []
    deps: dict[tuple[str, str], Dependency] = {}
    for path, eco in roots:
        own, found = eco.read(path)
        project_roots.append(ProjectRoot(path, eco.name, own))
        for d in found:
            deps.setdefault((d.ecosystem, d.name), d)
    start = start.resolve()
    first = roots[0][0]
    root = first if first == start or first in start.parents else start
    ordered = sorted(deps.values(), key=lambda d: (not d.direct, d.ecosystem, d.name))
    return Project(root, project_roots, ordered)


def github_url(text: str) -> str | None:
    """The first GitHub repo URL in `text`, normalized to https://github.com/<owner>/<repo>
    (from 'git+https://github.com/o/r.git', 'git@github.com:o/r', a page under the repo...)."""
    for owner, repo in _GITHUB.findall(text):
        if owner.lower() not in _NOT_OWNERS:
            return f"https://github.com/{owner}/{repo}"
    return None


def _version_variants(version: str) -> list[str]:
    """'2.1' also as '2.1.0'; '2.1.0' also as '2.1' (PEP 440 treats them as equal)."""
    variants = [version]
    if re.fullmatch(r"\d+\.\d+", version):
        variants.append(f"{version}.0")
    elif m := re.fullmatch(r"(\d+\.\d+)\.0", version):
        variants.append(m[1])
    return variants


def tag_candidates(package: str, version: str, pattern: str | None = None) -> list[str]:
    """Tag names to try, best first. `pattern` (from known_sources.toml) replaces the
    defaults; it may use {version}, {version_underscored} and {name}."""
    names = list(dict.fromkeys([package, package.replace("-", "_")]))
    out: list[str] = []
    for v in _version_variants(version):
        if pattern:
            fields = {"version": v, "version_underscored": v.replace(".", "_")}
            out += [pattern.format(name=n, **fields) for n in names]
            continue
        out += [v, f"v{v}"]
        for n in names:
            out += [f"{n}-{v}", f"{n}-v{v}", f"{n}@{v}", f"{n}/v{v}"]
        out += [f"release-{v}", f"release/{v}", f"rel-{v}"]
        # Rust repos that also ship a Python package (polars: py-2.0.0); the SQLAlchemy
        # family (alembic: rel_1_20_0).
        out += [f"py-{v}", f"py-v{v}", f"rel_{v.replace('.', '_')}"]
    return list(dict.fromkeys(out))


def parse_library(text: str) -> tuple[str | None, str, str]:
    """'npm:recharts@2.15.0' -> ('npm', 'recharts', '2.15.0'); 'typer' -> (None, 'typer', '');
    '@scope/pkg@1.0' -> (None, '@scope/pkg', '1.0'). A prefix names the ecosystem."""
    from grepwise.search.citations import split_ref

    eco, sep, rest = text.strip().partition(":")
    if not sep or eco not in _all():
        eco, rest = "", text.strip()
    name, version = split_ref(rest)
    return eco or None, name, version
