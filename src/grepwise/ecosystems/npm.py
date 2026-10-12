"""JavaScript and TypeScript projects and the npm registry (spec 16, section 4).

Declared dependencies come from package.json. Versions come from the first lockfile found at
the project or above it (a workspace's lockfile sits at its root): package-lock.json (or
npm-shrinkwrap.json), pnpm-lock.yaml, yarn.lock (classic or Berry); then the installed
node_modules/<name>/package.json. A package's repo, and its folder in a monorepo, come from
the registry's `repository` field.
"""

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from grepwise.ecosystems import Dependency, Ecosystem, HttpGet, RegistryInfo, github_url
from grepwise.ecosystems import tag_candidates as default_tag_candidates
from grepwise.ingest.filters import SKIP_DIRS

REGISTRY = "https://registry.npmjs.org/{name}/{version}"
GROUPS = ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies")
LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock")
# Versions that point somewhere other than the registry.
_NOT_REGISTRY = ("file:", "link:", "workspace:", "portal:", "git", "github:", "http", "patch:")
# 'github:owner/repo', 'owner/repo' (npm's shorthand for GitHub).
_SHORTHAND = re.compile(r"^(?:github:)?([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?(?:#.*)?$")
MAX_PACKAGE_DEPTH = 4  # how deep to look for a monorepo package's folder


def _json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text()) if path.is_file() else {}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text()) if path.is_file() else {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _from_registry(version: str) -> bool:
    return not version.startswith(_NOT_REGISTRY)


def find_lockfile(root: Path) -> Path | None:
    """The lockfile at `root` or the nearest folder above it, stopping below the home folder."""
    home = Path.home().resolve()
    for folder in [root.resolve(), *root.resolve().parents]:
        if folder == home or folder == folder.parent:
            return None
        for name in LOCKFILES:
            if (folder / name).is_file():
                return folder / name
    return None


def _package_lock(lock: Path, rel: str) -> dict[str, tuple[str, bool]]:
    """{name: (version, from the registry)}: this folder's own entries, then the hoisted ones
    at the workspace root. Nested node_modules/a/node_modules/b entries are other packages'
    private copies, so they're skipped."""
    data = _json(lock)
    out: dict[str, tuple[str, bool]] = {}
    packages = data.get("packages")
    if isinstance(packages, dict):  # lockfile v2/v3
        for prefix in ([f"{rel}/node_modules/"] if rel else []) + ["node_modules/"]:
            for key, entry in packages.items():
                if not key.startswith(prefix) or not isinstance(entry, dict):
                    continue
                name = key[len(prefix) :]
                if "node_modules/" in name or name in out or "version" not in entry:
                    continue
                resolved = str(entry.get("resolved", ""))
                registry = not entry.get("link") and not resolved.startswith(("git", "file:"))
                out[name] = (str(entry["version"]), registry)
    elif isinstance(data.get("dependencies"), dict):  # lockfile v1
        for name, entry in data["dependencies"].items():
            if isinstance(entry, dict) and "version" in entry:
                out[name] = (str(entry["version"]), _from_registry(str(entry["version"])))
    return out


def _pnpm(lock: Path, rel: str) -> dict[str, tuple[str, bool]]:
    data = _yaml(lock)
    importers = data.get("importers")
    importer = importers.get(rel or ".", {}) if isinstance(importers, dict) else data
    out: dict[str, tuple[str, bool]] = {}
    for group in GROUPS:
        for name, value in (importer.get(group) or {}).items():
            version = str(value.get("version", "")) if isinstance(value, dict) else str(value)
            version = version.split("(", 1)[0]  # '18.3.1(react@18.3.1)': the peer suffix
            if version:
                out[name] = (version, _from_registry(version))
    return out


def _yarn(lock: Path, declared: dict[str, str]) -> dict[str, tuple[str, bool]]:
    """Classic yarn.lock has its own format; Berry's is YAML. Either maps specs such as
    'react@^18.2.0' (Berry: 'react@npm:^18.2.0') to a version; pick the declared spec."""
    text = lock.read_text(errors="replace")
    versions: dict[str, str] = {}  # spec -> version
    if "__metadata:" in text:
        for key, entry in _yaml(lock).items():
            if isinstance(entry, dict) and "version" in entry:
                for spec in str(key).split(","):
                    versions[spec.strip().strip('"')] = str(entry["version"])
    else:
        specs: list[str] = []
        for line in text.splitlines():
            if line and not line.startswith((" ", "#")) and line.rstrip().endswith(":"):
                specs = [s.strip().strip('"') for s in line.rstrip()[:-1].split(",")]
            elif m := re.match(r'^\s+version\s+"?([^"\s]+)"?', line):
                versions.update((spec, m[1]) for spec in specs)
    out: dict[str, tuple[str, bool]] = {}
    for name, wanted in declared.items():
        for spec in (f"{name}@{wanted}", f"{name}@npm:{wanted}"):
            if spec in versions:
                out[name] = (versions[spec], _from_registry(wanted))
                break
        else:  # the range changed since the lock was written: any locked version of it
            found = [v for s, v in versions.items() if s.rsplit("@", 1)[0] == name]
            if found:
                out[name] = (found[0], _from_registry(wanted))
    return out


def _installed(root: Path, names: set[str]) -> dict[str, tuple[str, bool]]:
    out: dict[str, tuple[str, bool]] = {}
    for name in names:
        version = _json(root / "node_modules" / name / "package.json").get("version")
        if version:
            out[name] = (str(version), True)
    return out


def read(root: Path) -> tuple[str | None, list[Dependency]]:
    manifest = _json(root / "package.json")
    declared: dict[str, str] = {}
    for group in GROUPS:
        if isinstance(manifest.get(group), dict):
            for name, spec in manifest[group].items():
                declared.setdefault(name, str(spec))
    lock = find_lockfile(root)
    locked: dict[str, tuple[str, bool]] = {}
    label = None
    if lock is not None:
        rel = root.resolve().relative_to(lock.parent).as_posix()
        rel = "" if rel == "." else rel
        label = lock.name
        if lock.name == "pnpm-lock.yaml":
            locked = _pnpm(lock, rel)
        elif lock.name == "yarn.lock":
            locked = _yarn(lock, declared)
        else:
            locked = _package_lock(lock, rel)
    installed = _installed(root, set(declared) - set(locked))
    deps = []
    for name, spec in declared.items():
        if name in locked:
            (version, registry), source = locked[name], label
        elif name in installed:
            (version, registry), source = installed[name], "node_modules"
        else:
            version, registry, source = None, _from_registry(spec), None
        deps.append(Dependency(name, version, True, source, registry and _from_registry(spec),
                               "npm"))  # fmt: skip
    # Everything else at the top of package-lock.json is a transitive dependency.
    if lock is not None and lock.parent == root.resolve() and lock.name != "pnpm-lock.yaml":
        own = manifest.get("name")
        for name, (version, registry) in locked.items():
            if name not in declared and name != own:
                deps.append(Dependency(name, version, False, label, registry, "npm"))
    deps.sort(key=lambda d: (not d.direct, d.name))
    return (str(manifest["name"]) if manifest.get("name") else None), deps


def repository(info: dict[str, Any]) -> RegistryInfo:
    """The GitHub repo (and folder) from a version's registry metadata. `repository` can be a
    string ('github:o/r', 'o/r', a URL) or {url, directory}; then homepage, then bugs."""
    repo_field = info.get("repository")
    url, directory = (
        (repo_field.get("url", ""), repo_field.get("directory"))
        if isinstance(repo_field, dict)
        else (repo_field or "", None)
    )
    candidates = [str(url), str(info.get("homepage") or "")]
    bugs = info.get("bugs")
    candidates.append(str(bugs.get("url", "")) if isinstance(bugs, dict) else str(bugs or ""))
    for text in candidates:
        if m := _SHORTHAND.match(text.strip()):
            return RegistryInfo(f"https://github.com/{m[1]}/{m[2]}", _clean(directory))
        if repo := github_url(text):
            return RegistryInfo(repo, _clean(directory) if text == str(url) else None)
    return RegistryInfo(None)


def _clean(directory: object) -> str | None:
    folder = str(directory or "").strip().strip("/").removeprefix("./")
    return folder if folder and ".." not in folder.split("/") else None


def find_package_dir(root: Path, name: str, version: str | None = None) -> str | None:
    """The folder in a cloned monorepo whose package.json is `name` (when the registry didn't
    say), e.g. packages/playwright-core; None if it's the repo root or not found. Repos can
    hold stale copies with the same name (ag-grid's scripts/package.json is an old
    ag-grid-community), so a folder at `version` wins, then the shallowest."""
    if _json(root / "package.json").get("name") == name:
        return None
    found: list[tuple[bool, int, str]] = []  # (not at version, depth, folder)
    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        for child in sorted(folder.iterdir()):
            if not child.is_dir() or child.name.startswith(".") or child.name in SKIP_DIRS:
                continue
            manifest = _json(child / "package.json")
            if manifest.get("name") == name:
                at_version = version is not None and str(manifest.get("version")) == version
                found.append((not at_version, depth, child.relative_to(root).as_posix()))
            elif depth + 1 < MAX_PACKAGE_DEPTH:
                stack.append((child, depth + 1))
    return min(found)[2] if found else None


class Npm(Ecosystem):
    name = "npm"
    registry = "npm"

    def is_root(self, folder: Path) -> bool:
        return (folder / "package.json").is_file()

    def read(self, root: Path) -> tuple[str | None, list[Dependency]]:
        return read(root)

    def normalize(self, name: str) -> str:
        return name.strip().lower()

    def unsupported(self, name: str) -> str | None:
        if name.startswith("@types/"):
            return (
                "type definitions live in DefinitelyTyped, which has no per-package tags; "
                "read the library itself instead"
            )
        return None

    def registry_info(self, name: str, version: str, http_get: HttpGet) -> RegistryInfo | None:
        data = http_get(REGISTRY.format(name=quote(name, safe="@"), version=quote(version)))
        return None if data is None else repository(data)

    def latest_version(self, name: str, http_get: HttpGet) -> str | None:
        data = http_get(REGISTRY.format(name=quote(name, safe="@"), version="latest"))
        return None if data is None else str(data.get("version") or "")

    def tag_candidates(self, name: str, version: str, pattern: str | None = None) -> list[str]:
        """Monorepo tags name the package ('@scope/pkg@1.2.3', the changesets convention, or
        'pkg@1.2.3'), so those come before the plain 'v1.2.3' a repo may use for another
        package."""
        unscoped = name.rsplit("/", 1)[-1]
        if pattern:
            return default_tag_candidates(unscoped, version, pattern)
        specific = [f"{name}@{version}", f"{unscoped}@{version}", f"{unscoped}@v{version}"]
        return list(dict.fromkeys(specific + default_tag_candidates(unscoped, version)))

    def locate_package(self, root: Path, name: str, version: str | None = None) -> str | None:
        return find_package_dir(root, name, version)


NPM = Npm()
