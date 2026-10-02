"""Fetch a public GitHub repo at a tag or branch into a temporary folder."""

import os
import re
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

CLONE_TIMEOUT_SECONDS = 300

_GITHUB_URL = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9-]+)/(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?/?$"
)
_SAFE_REF = re.compile(r"^[A-Za-z0-9._/-]+$")


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class RepoRef:
    url: str  # normalised clone URL
    name: str  # 'owner/repo'


@dataclass(frozen=True)
class Checkout:
    path: Path
    commit_sha: str


def parse_github_url(url: str) -> RepoRef:
    """Accept only public-looking `https://github.com/<owner>/<repo>` URLs."""
    match = _GITHUB_URL.match(url.strip())
    if not match:
        raise FetchError(
            f"Not a GitHub repo URL: {url!r}. Expected https://github.com/<owner>/<repo>."
        )
    name = f"{match['owner']}/{match['repo']}"
    return RepoRef(url=f"https://github.com/{name}", name=name)


def validate_ref(ref: str) -> str:
    if not _SAFE_REF.match(ref) or ref.startswith("-") or ".." in ref:
        raise FetchError(f"Invalid ref: {ref!r}. Use a tag or branch name.")
    return ref


def _git(*args: str, cwd: Path | None = None, action: str = "git") -> str:
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",  # private/missing repos fail instead of asking for a password
        "GIT_LFS_SKIP_SMUDGE": "1",
    }
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=CLONE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        # git's last stderr line is the useful one, e.g. "fatal: Remote branch x not found".
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "no output"
        raise FetchError(f"{action} failed: {detail}")
    return result.stdout.strip()


@contextmanager
def clone(url: str, ref: str) -> Generator[Checkout]:
    """Shallow-clone any git URL at `ref`. The folder is deleted on exit, even on error.

    Callers outside tests should use `checkout()`, which validates the URL first.
    """
    validate_ref(ref)
    with tempfile.TemporaryDirectory(prefix="grepwise-") as tmp:
        path = Path(tmp) / "repo"
        _git(
            # Symlinks are checked out as plain files, so nothing can point outside the clone.
            "-c", "core.symlinks=false",
            "clone", "--depth", "1", "--single-branch", "--branch", ref,
            "--", url, str(path),
            action=f"Cloning {url} at {ref!r}",
        )  # fmt: skip
        yield Checkout(path=path, commit_sha=_git("rev-parse", "HEAD", cwd=path))


@contextmanager
def checkout(url: str, ref: str) -> Generator[Checkout]:
    """Clone a public GitHub repo at a tag or branch."""
    repo = parse_github_url(url)
    with clone(repo.url, ref) as co:
        yield co
