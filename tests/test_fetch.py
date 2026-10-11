import subprocess
from pathlib import Path

import pytest

from grepwise.ingest.fetch import FetchError, clone, parse_github_url, validate_ref


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("https://github.com/pallets/itsdangerous", "pallets/itsdangerous"),
        ("https://github.com/pallets/itsdangerous.git", "pallets/itsdangerous"),
        ("https://github.com/vercel/next.js/", "vercel/next.js"),
    ],
)
def test_accepts_github_urls(url: str, name: str) -> None:
    ref = parse_github_url(url)
    assert ref.name == name and ref.url == f"https://github.com/{name}"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b",
        "https://gitlab.com/a/b",
        "git@github.com:a/b.git",
        "https://github.com/a",
        "https://github.com/a/b/tree/main",
        "file:///etc",
        "https://github.com.evil.com/a/b",
    ],
)
def test_rejects_other_urls(url: str) -> None:
    with pytest.raises(FetchError):
        parse_github_url(url)


@pytest.mark.parametrize("ref", ["--upload-pack=x", "a..b", "a b", ""])
def test_rejects_unsafe_refs(ref: str) -> None:
    with pytest.raises(FetchError):
        validate_ref(ref)


@pytest.fixture
def local_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "origin"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    for cmd in (
        ["init", "-q", "-b", "main"],
        ["add", "."],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
        ["tag", "v1"],
    ):
        subprocess.run(["git", *cmd], cwd=repo, check=True)
    return repo


def test_clone_records_sha_and_cleans_up(local_repo: Path) -> None:
    with clone(local_repo.as_uri(), "v1") as co:
        assert (co.path / "a.py").read_text() == "x = 1\n"
        assert len(co.commit_sha) == 40
        tmp = co.path.parent
    assert not tmp.exists()


def test_clone_cleans_up_on_error(local_repo: Path) -> None:
    with pytest.raises(RuntimeError), clone(local_repo.as_uri(), "v1") as co:
        tmp = co.path.parent
        raise RuntimeError("boom")
    assert not tmp.exists()


def test_clone_of_missing_ref_fails_clearly(local_repo: Path) -> None:
    with (
        pytest.raises(FetchError, match="Remote branch nope not found"),
        clone(local_repo.as_uri(), "nope"),
    ):
        pass
