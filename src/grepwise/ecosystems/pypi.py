"""Python projects and PyPI (spec 12, section 3; spec 16).

Declared (direct) dependencies come from pyproject.toml and requirements*.txt. Versions come
from the first of uv.lock, poetry.lock, pinned requirements lines and the installed .venv
that has the package. Everything else in a lockfile is a transitive dependency. A package's
repo comes from its PyPI page (project_urls).
"""

import re
import tomllib
from pathlib import Path
from typing import Any

from grepwise.ecosystems import Dependency, Ecosystem, HttpGet, RegistryInfo, github_url

PYPI_RELEASE = "https://pypi.org/pypi/{name}/{version}/json"
PYPI_LATEST = "https://pypi.org/pypi/{name}/json"
_MARKERS = ("pyproject.toml", "uv.lock", "poetry.lock", "setup.py")
_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_PIN = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*===?\s*([A-Za-z0-9._+!-]+)\s*(?:;.*)?$"
)
# project_urls keys most likely to name the source repo, best first (matched as substrings).
_URL_KEYS = ("source", "repository", "code", "github", "homepage", "home")


def normalize_name(name: str) -> str:
    """PEP 503: 'Markdown_It.py' -> 'markdown-it-py'."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_files(root: Path) -> list[Path]:
    return sorted(p for p in root.glob("requirements*.txt") if p.is_file())


def _toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text()) if path.is_file() else {}
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
        return {}


def _names(specs: object) -> set[str]:
    """Names from PEP 508 strings ('typer>=0.9', 'rich[jupyter]; python_version>"3.9"')."""
    out = set()
    for spec in specs if isinstance(specs, list) else []:
        if isinstance(spec, str) and (m := _NAME.match(spec)):
            out.add(normalize_name(m[1]))
    return out


def declared(root: Path) -> tuple[str | None, set[str]]:
    """(the project's own name, the names it declares as dependencies)."""
    data = _toml(root / "pyproject.toml")
    project = data.get("project", {})
    poetry = data.get("tool", {}).get("poetry", {})
    names = _names(project.get("dependencies"))
    for group in project.get("optional-dependencies", {}).values():
        names |= _names(group)
    for group in data.get("dependency-groups", {}).values():
        names |= _names(group)  # {include-group = ...} entries aren't strings: skipped
    names |= _names(data.get("tool", {}).get("uv", {}).get("dev-dependencies"))
    for table in (poetry.get("dependencies", {}), poetry.get("dev-dependencies", {})):
        names |= {normalize_name(n) for n in table}
    for group in poetry.get("group", {}).values():
        names |= {normalize_name(n) for n in group.get("dependencies", {})}
    for req in _requirement_files(root):
        lines = req.read_text(errors="replace").splitlines()
        names |= _names(
            [ln for ln in lines if ln.strip() and not ln.lstrip().startswith(("#", "-"))]
        )
    own = project.get("name") or poetry.get("name")
    names.discard("python")
    if own:
        names.discard(normalize_name(own))
    return (normalize_name(own) if own else None), names


def _locked_uv(root: Path) -> dict[str, tuple[str, bool]]:
    out: dict[str, tuple[str, bool]] = {}
    for pkg in _toml(root / "uv.lock").get("package", []):
        source = pkg.get("source", {})
        if "editable" in source or "virtual" in source:
            continue  # the project itself
        if "name" in pkg and "version" in pkg:
            out[normalize_name(pkg["name"])] = (str(pkg["version"]), "registry" in source)
    return out


def _locked_poetry(root: Path) -> dict[str, tuple[str, bool]]:
    out: dict[str, tuple[str, bool]] = {}
    for pkg in _toml(root / "poetry.lock").get("package", []):
        if "name" in pkg and "version" in pkg:
            out[normalize_name(pkg["name"])] = (str(pkg["version"]), "source" not in pkg)
    return out


def _pinned(root: Path) -> dict[str, tuple[str, bool]]:
    out: dict[str, tuple[str, bool]] = {}
    for req in _requirement_files(root):
        for line in req.read_text(errors="replace").splitlines():
            if m := _PIN.match(line):
                out.setdefault(normalize_name(m[1]), (m[2], True))
    return out


def _installed(root: Path) -> dict[str, tuple[str, bool]]:
    out: dict[str, tuple[str, bool]] = {}
    site_dirs = [*root.glob(".venv/lib/python*/site-packages"), root / ".venv/Lib/site-packages"]
    for site in site_dirs:
        for meta in site.glob("*.dist-info/METADATA"):
            name = version = None
            for line in meta.read_text(errors="replace").splitlines()[:20]:
                if line.startswith("Name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("Version:"):
                    version = line.split(":", 1)[1].strip()
            if name and version:
                out.setdefault(normalize_name(name), (version, True))
    return out


def read(root: Path) -> tuple[str | None, list[Dependency]]:
    own, direct = declared(root)
    sources = [
        ("uv.lock", _locked_uv(root)),
        ("poetry.lock", _locked_poetry(root)),
        ("requirements.txt", _pinned(root)),
        (".venv", _installed(root)),
    ]
    names = set(direct)
    lockfile_names = {n for label, found in sources if label != ".venv" for n in found}
    names |= lockfile_names or {n for _, found in sources for n in found}
    deps = []
    for name in names - ({own} if own else set()):
        version, label, pypi = None, None, True
        for source_label, found in sources:
            if name in found:
                (version, pypi), label = found[name], source_label
                break
        deps.append(Dependency(name, version, name in direct, label, pypi, "pypi"))
    deps.sort(key=lambda d: (not d.direct, d.name))
    return own, deps


def github_repo(info: dict[str, Any]) -> str | None:
    """The GitHub repo in a release's PyPI metadata, preferring keys like 'Source'."""
    urls = {str(k).lower(): v for k, v in (info.get("project_urls") or {}).items() if v}
    ordered = [v for key in _URL_KEYS for k, v in urls.items() if key in k]
    ordered += [info.get("home_page") or "", *urls.values(), info.get("download_url") or ""]
    for url in ordered:
        if repo := github_url(str(url)):
            return repo
    return None


class PyPI(Ecosystem):
    name = "pypi"
    registry = "PyPI"

    def is_root(self, folder: Path) -> bool:
        return any((folder / m).is_file() for m in _MARKERS) or bool(_requirement_files(folder))

    def read(self, root: Path) -> tuple[str | None, list[Dependency]]:
        return read(root)

    def normalize(self, name: str) -> str:
        return normalize_name(name)

    def registry_info(self, name: str, version: str, http_get: HttpGet) -> RegistryInfo | None:
        data = http_get(PYPI_RELEASE.format(name=normalize_name(name), version=version))
        return None if data is None else RegistryInfo(github_repo(data.get("info", {})))

    def latest_version(self, name: str, http_get: HttpGet) -> str | None:
        data = http_get(PYPI_LATEST.format(name=normalize_name(name)))
        return None if data is None else str(data.get("info", {}).get("version") or "")


PYPI = PyPI()
