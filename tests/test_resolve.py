"""Package -> GitHub repo -> tag, with a fake PyPI and fake tag lists (no network)."""

from pathlib import Path
from typing import Any

import pytest

from grepwise.config import get_settings
from grepwise.db.connection import Conn
from grepwise.ecosystems import tag_candidates
from grepwise.ecosystems.pypi import github_repo
from grepwise.resolve import (
    ResolveError,
    Resolver,
    closest_tags,
    load_known_sources,
    pick_tag,
)

# Shaped like real PyPI responses (pydantic-settings and click, October 2026).
PYDANTIC_SETTINGS = {
    "project_urls": {
        "Changelog": "https://github.com/pydantic/pydantic-settings/releases",
        "Documentation": "https://docs.pydantic.dev/dev-v2/concepts/pydantic_settings/",
        "Funding": "https://github.com/sponsors/samuelcolvin",
        "Homepage": "https://github.com/pydantic/pydantic-settings",
        "Source": "https://github.com/pydantic/pydantic-settings",
    },
    "home_page": None,
}


def test_github_repo_prefers_source_keys_and_skips_sponsor_links() -> None:
    assert github_repo(PYDANTIC_SETTINGS) == "https://github.com/pydantic/pydantic-settings"
    assert github_repo({"project_urls": {"Funding": "https://github.com/sponsors/x"}}) is None
    click = {"project_urls": {"Donate": "https://palletsprojects.com/donate",
                              "Source": "https://github.com/pallets/click/"}}  # fmt: skip
    assert github_repo(click) == "https://github.com/pallets/click"
    tracker = {"project_urls": {"Bug Tracker": "https://github.com/yaml/pyyaml/issues"},
               "home_page": "https://pyyaml.org/"}  # fmt: skip
    assert github_repo(tracker) == "https://github.com/yaml/pyyaml"  # any GitHub link, last
    assert github_repo({"home_page": "git@github.com:owner/repo.git"}) == (
        "https://github.com/owner/repo"
    )
    assert github_repo({"project_urls": {"Homepage": "https://gitlab.com/a/b"}}) is None


@pytest.mark.parametrize(
    ("tags", "package", "version", "expected"),
    [
        (["0.27.1", "0.27.2"], "typer", "0.27.2", "0.27.2"),
        (["v2.5.2", "v2.5.3"], "numpy", "2.5.3", "v2.5.3"),
        (["pydantic-settings-2.0", "x"], "pydantic-settings", "2.0", "pydantic-settings-2.0"),
        (["my_pkg-v1.2.0"], "my-pkg", "1.2.0", "my_pkg-v1.2.0"),  # underscored name
        (["core@3.1.0"], "core", "3.1.0", "core@3.1.0"),
        (["v1.0.0"], "lib", "1.0", "v1.0.0"),  # 1.0 == 1.0.0
        (["V4.0"], "lib", "4.0", "V4.0"),  # case-insensitive fallback
        (["py-1.9.0", "py-2.0.0", "rs-0.53.0"], "polars", "2.0.0", "py-2.0.0"),
        (["rel_1_19_2", "rel_1_20_0"], "alembic", "1.20.0", "rel_1_20_0"),
        (["v1.0", "v2.0"], "lib", "3.0", None),
    ],
)
def test_pick_tag(tags: list[str], package: str, version: str, expected: str | None) -> None:
    assert pick_tag(tags, tag_candidates(package, version)) == expected


def test_tag_pattern_override() -> None:
    assert tag_candidates("sqlalchemy", "2.1.4", "rel_{version_underscored}")[0] == "rel_2_1_4"
    assert load_known_sources()["sqlalchemy"]["tag"] == "rel_{version_underscored}"


def test_user_known_sources_win(tmp_path: Path) -> None:
    (tmp_path / "known_sources.toml").write_text(
        '[SQLAlchemy]\ntag = "v{version}"\n[internal_lib]\nrepo = "https://github.com/me/lib"\n'
    )
    known = load_known_sources(tmp_path)
    assert known["sqlalchemy"]["tag"] == "v{version}"
    assert known["internal-lib"]["repo"] == "https://github.com/me/lib"


def test_a_broken_user_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "known_sources.toml").write_text('[lib\nrepo = "x"\n')  # bad TOML
    assert load_known_sources(tmp_path)["sqlalchemy"]["tag"] == "rel_{version_underscored}"
    (tmp_path / "known_sources.toml").write_text('lib = "x"\n[ok]\nrepo = "y"\n')  # not a table
    known = load_known_sources(tmp_path)
    assert "lib" not in known and known["ok"]["repo"] == "y"


def test_the_resolver_reads_the_users_known_sources_by_default(
    conn: Conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It used to load only the built-in file, so entries users were told to add did nothing."""
    monkeypatch.setenv("GREPWISE_HOME", str(tmp_path))
    get_settings.cache_clear()
    (tmp_path / "known_sources.toml").write_text(
        '[internal_lib]\nrepo = "https://github.com/me/lib"\n'
    )
    try:
        known = Resolver(conn).known
    finally:
        get_settings.cache_clear()
    assert known["internal-lib"]["repo"] == "https://github.com/me/lib"
    assert known["sqlalchemy"]["tag"] == "rel_{version_underscored}"  # built-in kept


class FakePyPI:
    def __init__(self, releases: dict[str, dict[str, Any]]):
        self.releases, self.calls = releases, 0

    def __call__(self, url: str) -> dict[str, Any] | None:
        self.calls += 1
        for key, info in self.releases.items():
            if f"/{key}/json" in url:
                return {"info": info}
        return None


def resolver(conn: Conn, pypi: FakePyPI, tags: list[str], **kw: Any) -> Resolver:
    return Resolver(conn, known={}, http_get=pypi, tags=lambda _repo: tags, **kw)


def test_resolve_and_cache(conn: Conn) -> None:
    pypi = FakePyPI({"pydantic-settings/2.15.0": PYDANTIC_SETTINGS})
    first = resolver(conn, pypi, ["v2.14.0", "v2.15.0"]).resolve("Pydantic_Settings", "2.15.0")
    assert (first.package, first.repo_url, first.tag, first.cached) == (
        "pydantic-settings", "https://github.com/pydantic/pydantic-settings", "v2.15.0", False,
    )  # fmt: skip
    again = resolver(conn, pypi, []).resolve("pydantic-settings", "2.15.0")
    assert again.cached and again.tag == "v2.15.0" and pypi.calls == 1  # no network
    fresh = resolver(conn, pypi, ["v2.15.0"], refresh=True).resolve("pydantic-settings", "2.15.0")
    assert not fresh.cached and pypi.calls == 2


def test_resolve_errors_explain_what_to_do(conn: Conn) -> None:
    pypi = FakePyPI({"lib/3.0": {"project_urls": {"Source": "https://github.com/o/lib"}},
                     "nolink/1.0": {"project_urls": {"Home": "https://example.com"}}})  # fmt: skip
    with pytest.raises(
        ResolveError, match=r"closest: v2\.9.*grepwise add https://github\.com/o/lib"
    ):
        resolver(conn, pypi, ["v1.0", "v2.9"]).resolve("lib", "3.0")
    with pytest.raises(ResolveError, match="links no GitHub repo"):
        resolver(conn, pypi, []).resolve("nolink", "1.0")
    with pytest.raises(ResolveError, match="not on PyPI"):
        resolver(conn, pypi, []).resolve("missing", "1.0")
    assert conn.execute("SELECT count(*) FROM resolutions").fetchone() == (
        0,
    )  # failures aren't cached


def test_known_repo_override_skips_pypi_links(conn: Conn) -> None:
    pypi = FakePyPI({"lib/1.0": {"project_urls": {}}})
    repo = "https://github.com/me/lib"
    r = Resolver(conn, known={"lib": {"repo": repo}}, http_get=pypi,
                 tags=lambda url: ["1.0"] if url == repo else [])  # fmt: skip
    assert r.resolve("lib", "1.0").repo_url == "https://github.com/me/lib"


def test_closest_tags_are_numerically_near() -> None:
    tags = ["v1.0", "v2.9", "v3.1", "latest", "v10.0", "2.9.9"]
    assert closest_tags(tags, "3.0", n=3) == ["v3.1", "v2.9", "2.9.9"]
