"""From a package and version to the GitHub repo and git tag of that exact release (spec 12,
section 4; spec 16). The registry lookup is the ecosystem's (PyPI for Python); the cache,
`git ls-remote` and tag matching are shared. A missing tag is an error, never a guess: indexing
the wrong version would break Grepwise's main promise.
"""

import logging
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from itertools import zip_longest
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from grepwise.config import get_settings
from grepwise.db.connection import Conn
from grepwise.ecosystems import Ecosystem, HttpGet, ecosystems, get_ecosystem
from grepwise.ingest import fetch

HTTP_TIMEOUT_SECONDS = 10
log = logging.getLogger(__name__)

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
    subpath: str | None = None  # the package's folder, in a repo holding several packages
    ecosystem: str = "pypi"


def http_get_json(url: str) -> dict[str, Any] | None:
    host = urlparse(url).hostname or url
    try:
        response = httpx.get(url, timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise ResolveError(f"Couldn't reach {host}: {exc}") from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise ResolveError(f"{host} returned {response.status_code} for {url}")
    data: dict[str, Any] = response.json()
    return data


def list_tags(repo_url: str) -> list[str]:
    """Tag names, via `git ls-remote` (no clone)."""
    out = fetch.git("ls-remote", "--tags", "--refs", repo_url, action=f"Listing tags of {repo_url}")
    return [line.split("refs/tags/", 1)[1] for line in out.splitlines() if "refs/tags/" in line]


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


def load_known_sources(
    home: Path | None = None, ecosystem: str = "pypi"
) -> dict[str, dict[str, str]]:
    """Per-package overrides for one ecosystem: the built-in file, then
    GREPWISE_HOME/known_sources.toml. Top-level tables are PyPI packages; another ecosystem's
    go under its name (`[npm."@scope/pkg"]`)."""
    eco = get_ecosystem(ecosystem)
    others = {e.name for e in ecosystems()} - {"pypi"}
    texts = [resources.files("grepwise").joinpath("known_sources.toml").read_text()]
    user = home / "known_sources.toml" if home else None
    if user and user.is_file():
        texts.append(user.read_text())
    known: dict[str, dict[str, str]] = {}
    for text in texts:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:  # a typo in the user's file mustn't stop all
            log.warning("Ignoring %s: %s", user, exc)  # resolution; only that file is skipped
            continue
        tables = (
            {k: v for k, v in data.items() if k not in others}
            if ecosystem == "pypi"
            else data.get(ecosystem, {})
        )
        for k, v in tables.items():
            if isinstance(v, dict):  # `name = "..."` instead of a [name] table: ignored
                known.setdefault(eco.normalize(k), {}).update(v)
    return known


class Resolver:
    def __init__(
        self,
        conn: Conn,
        *,
        ecosystem: str = "pypi",
        known: dict[str, dict[str, str]] | None = None,
        http_get: HttpGet = http_get_json,
        tags: ListTags = list_tags,
        refresh: bool = False,
    ):
        self.conn = conn
        self.eco: Ecosystem = get_ecosystem(ecosystem)
        if known is None:  # the built-in overrides, then the user's own (which win)
            known = load_known_sources(get_settings().home, ecosystem)
        self.known = known
        self.http_get = http_get
        self.tags = tags
        self.refresh = refresh

    def latest_version(self, package: str) -> str:
        version = self.eco.latest_version(package, self.http_get)
        if version is None:
            raise ResolveError(f"No package named {package!r} on {self.eco.registry}.")
        return version

    def on_registry(self, package: str, version: str | None = None) -> bool:
        """Whether the registry has this package (at this version, if given). ResolveError if
        it can't be reached."""
        if version:
            return self.eco.registry_info(package, version, self.http_get) is not None
        return self.eco.latest_version(package, self.http_get) is not None

    def resolve(self, package: str, version: str) -> Resolution:
        eco = self.eco
        name = eco.normalize(package)
        if not self.refresh and (hit := self._cached(name, version)):
            return hit
        if why := eco.unsupported(name):
            raise ResolveError(f"{name} isn't indexed: {why}.")
        override = self.known.get(name, {})
        info = eco.registry_info(name, version, self.http_get)
        if info is None:
            raise ResolveError(f"{name} {version} is not on {eco.registry}.")
        repo = override.get("repo") or info.repo
        if repo is None:
            raise ResolveError(
                f"{name}'s {eco.registry} page links no GitHub repo. Add it to "
                "~/.grepwise/known_sources.toml, or use grepwise add <github-url> --ref <tag>."
            )
        subpath = override.get("subpath") or info.subpath
        tags = self.tags(repo)
        tag = pick_tag(tags, eco.tag_candidates(name, version, override.get("tag")))
        if tag is None:
            close = closest_tags(tags, version)
            raise ResolveError(
                f"No tag for {name} {version} in {repo}"
                + (f" (closest: {', '.join(close)})" if close else " (it has no tags)")
                + f". Use grepwise add {repo} --ref <tag>."
            )
        self.conn.execute(
            "INSERT OR REPLACE INTO resolutions "
            "(ecosystem, package, version, repo_url, tag, subpath) VALUES (?, ?, ?, ?, ?, ?)",
            (eco.name, name, version, repo, tag, subpath),
        )
        return Resolution(name, version, repo, tag, subpath=subpath, ecosystem=eco.name)

    def _cached(self, name: str, version: str) -> Resolution | None:
        row = self.conn.execute(
            "SELECT repo_url, tag, subpath FROM resolutions "
            "WHERE ecosystem = ? AND package = ? AND version = ?",
            (self.eco.name, name, version),
        ).fetchone()
        if row is None:
            return None
        return Resolution(name, version, row[0], row[1], cached=True, subpath=row[2],
                          ecosystem=self.eco.name)  # fmt: skip
