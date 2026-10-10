"""From a PyPI package and version to the GitHub repo and git tag of that exact release
(spec 12, section 4). A missing tag is an error, never a guess: indexing the wrong version
would break Grepwise's main promise.
"""

import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from itertools import zip_longest
from pathlib import Path
from typing import Any

import httpx

from grepwise.db.connection import Conn
from grepwise.ingest import fetch
from grepwise.project import normalize_name

PYPI_RELEASE = "https://pypi.org/pypi/{name}/{version}/json"
PYPI_LATEST = "https://pypi.org/pypi/{name}/json"
HTTP_TIMEOUT_SECONDS = 10

_GITHUB = re.compile(
    r"github\.com[/:]([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?(?:[/#?]|$)", re.I
)
# github.com/<these>/... are GitHub pages, not repos (e.g. a "Funding" link to /sponsors/x).
_NOT_OWNERS = {"sponsors", "orgs", "apps", "marketplace", "features", "topics", "about", "site"}
# project_urls keys most likely to name the source repo, best first (matched as substrings).
_URL_KEYS = ("source", "repository", "code", "github", "homepage", "home")

HttpGet = Callable[[str], dict[str, Any] | None]  # parsed JSON, or None for a 404
ListTags = Callable[[str], list[str]]


class ResolveError(Exception):
    pass


@dataclass(frozen=True)
class Resolution:
    package: str  # normalized
    version: str
    repo_url: str  # https://github.com/<owner>/<repo>
    tag: str
    cached: bool = False


def http_get_json(url: str) -> dict[str, Any] | None:
    try:
        response = httpx.get(url, timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise ResolveError(f"Couldn't reach PyPI: {exc}") from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise ResolveError(f"PyPI returned {response.status_code} for {url}")
    data: dict[str, Any] = response.json()
    return data


def list_tags(repo_url: str) -> list[str]:
    """Tag names, via `git ls-remote` (no clone)."""
    out = fetch.git("ls-remote", "--tags", "--refs", repo_url, action=f"Listing tags of {repo_url}")
    return [line.split("refs/tags/", 1)[1] for line in out.splitlines() if "refs/tags/" in line]


def github_repo(info: dict[str, Any]) -> str | None:
    """The GitHub repo in a release's PyPI metadata, preferring keys like 'Source'."""
    urls = {str(k).lower(): v for k, v in (info.get("project_urls") or {}).items() if v}
    ordered = [v for key in _URL_KEYS for k, v in urls.items() if key in k]
    ordered += [info.get("home_page") or "", *urls.values(), info.get("download_url") or ""]
    for url in ordered:
        for owner, repo in _GITHUB.findall(str(url)):
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
    return list(dict.fromkeys(out))


def pick_tag(tags: list[str], candidates: list[str]) -> str | None:
    exact = set(tags)
    for c in candidates:
        if c in exact:
            return c
    lowered = {t.lower(): t for t in tags}
    return next((lowered[c.lower()] for c in candidates if c.lower() in lowered), None)


def closest_tags(tags: list[str], version: str, n: int = 5) -> list[str]:
    """Tags nearest to `version` numerically: same major first, then minor, then patch."""
    wanted = [int(x) for x in re.findall(r"\d+", version)]

    def distance(tag: str) -> list[int]:
        numbers = [int(x) for x in re.findall(r"\d+", tag)]
        return [abs(a - b) for a, b in zip_longest(wanted, numbers, fillvalue=0)]

    return sorted((t for t in tags if re.search(r"\d", t)), key=distance)[:n]


def load_known_sources(home: Path | None = None) -> dict[str, dict[str, str]]:
    """Per-package overrides: the built-in file, then GREPWISE_HOME/known_sources.toml."""
    text = resources.files("grepwise").joinpath("known_sources.toml").read_text()
    known: dict[str, dict[str, str]] = {
        normalize_name(k): dict(v) for k, v in tomllib.loads(text).items()
    }
    user = home / "known_sources.toml" if home else None
    if user and user.is_file():
        for k, v in tomllib.loads(user.read_text()).items():
            known.setdefault(normalize_name(k), {}).update(v)
    return known


class Resolver:
    def __init__(
        self,
        conn: Conn,
        *,
        known: dict[str, dict[str, str]] | None = None,
        http_get: HttpGet = http_get_json,
        tags: ListTags = list_tags,
        refresh: bool = False,
    ):
        self.conn = conn
        self.known = known if known is not None else load_known_sources()
        self.http_get = http_get
        self.tags = tags
        self.refresh = refresh

    def latest_version(self, package: str) -> str:
        data = self.http_get(PYPI_LATEST.format(name=normalize_name(package)))
        if data is None:
            raise ResolveError(f"No package named {package!r} on PyPI.")
        return str(data["info"]["version"])

    def resolve(self, package: str, version: str) -> Resolution:
        name = normalize_name(package)
        if not self.refresh and (hit := self._cached(name, version)):
            return hit
        override = self.known.get(name, {})
        data = self.http_get(PYPI_RELEASE.format(name=name, version=version))
        if data is None:
            raise ResolveError(f"{name} {version} is not on PyPI.")
        repo = override.get("repo") or github_repo(data.get("info", {}))
        if repo is None:
            raise ResolveError(
                f"{name}'s PyPI page links no GitHub repo. Add it to "
                "~/.grepwise/known_sources.toml, or use grepwise add <github-url> --ref <tag>."
            )
        tags = self.tags(repo)
        tag = pick_tag(tags, tag_candidates(name, version, override.get("tag")))
        if tag is None:
            close = closest_tags(tags, version)
            raise ResolveError(
                f"No tag for {name} {version} in {repo}"
                + (f" (closest: {', '.join(close)})" if close else " (it has no tags)")
                + f". Use grepwise add {repo} --ref <tag>."
            )
        self.conn.execute(
            "INSERT OR REPLACE INTO resolutions (ecosystem, package, version, repo_url, tag) "
            "VALUES ('pypi', ?, ?, ?, ?)",
            (name, version, repo, tag),
        )
        return Resolution(name, version, repo, tag)

    def _cached(self, name: str, version: str) -> Resolution | None:
        row = self.conn.execute(
            "SELECT repo_url, tag FROM resolutions "
            "WHERE ecosystem = 'pypi' AND package = ? AND version = ?",
            (name, version),
        ).fetchone()
        return Resolution(name, version, row[0], row[1], cached=True) if row else None
