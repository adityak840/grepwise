# N2: Project awareness

Part of [10-v2-plan.md](10-v2-plan.md). **Goal:** Grepwise knows which libraries your Python project uses, at which versions, and can index any of them by name: `grepwise add typer`, or `grepwise sync` for all of them. No looking up repos, tags or docs folders by hand. The MCP tools stay as they are until N3.

## 1. One source per library

Today a library is indexed twice (`index` for code, `index-docs` for docs). Two clones, and duplicate results whenever the repo also contains the docs folder.

- **The repo indexer chunks each file by its kind:** `.md` and `.mdx` with the Markdown splitter, `.rst` with a new reStructuredText splitter, code with tree-sitter, anything else with the fallback windows. Docs sections get the docs header (`Docs: <name>`, file, section path).
- **Sphinx `.txt` files** (Django writes its docs as `.txt` in reST) are chunked as reST when they sit under a folder containing a Sphinx `conf.py`.
- `index-docs` stays, for docs that live in their own repo or when only a subfolder is wanted.
- **Search drops duplicate citations** (same `name@version:path:lines` from two sources), keeping the best-ranked one.

## 2. reStructuredText splitter

Same output as the Markdown splitter: one `section` chunk per heading, symbol = heading path, oversized sections windowed, text before the first heading named after the file.

- A heading is a non-indented title line followed by an underline of one repeated punctuation character (`= - ~ ^ " ' \` # * + : . _`) at least as long as the title, optionally with a matching overline.
- Levels follow the order in which underline styles first appear (as in docutils). Overline + underline is a different style from underline alone.
- Not headings: indented lines (literal blocks, directive bodies), punctuation-only title lines, lines with spaces in the underline (table borders).

## 3. Reading the project

`grepwise.project`: find the project root (walk up from the working directory to the first folder with `pyproject.toml`, `uv.lock`, `poetry.lock`, `requirements*.txt` or `setup.py`) and list its dependencies with exact versions.

- **Declared (direct) dependencies:** `[project].dependencies`, `[project.optional-dependencies]`, `[dependency-groups]`, and `[tool.poetry.*dependencies]` in `pyproject.toml`; names from `requirements*.txt`.
- **Versions, first that has the package:** `uv.lock`, `poetry.lock`, pinned `name==version` lines in `requirements*.txt`, then the installed `.venv` (`*.dist-info/METADATA`).
- Names are normalized (PEP 503: lowercase, runs of `-_.` become `-`). Packages from git or path sources in a lockfile are listed but marked "not from PyPI".
- Transitive dependencies (in the lockfile but not declared) are listed too, marked as such.

## 4. Resolving a package to a repo and tag

`grepwise.resolve`: `(package, version) → (GitHub repo URL, git tag)`.

1. **Repo:** PyPI's JSON API for that release (`https://pypi.org/pypi/<name>/<version>/json`). From `project_urls`, take the first GitHub URL under a key like Source, Repository, Code, GitHub, Homepage (case-insensitive), then `home_page`, then any GitHub URL. Normalize to `https://github.com/<owner>/<repo>`.
2. **Tag:** `git ls-remote --tags --refs <repo>`, then the first match among `<v>`, `v<v>`, `<name>-<v>`, `<name>-v<v>`, `<name>@<v>`, `release-<v>` (name in its normalized, underscored and original forms; `X.Y` also tried as `X.Y.0`, and `X.Y.0` as `X.Y`).
3. **No match is an error, never a guess:** the message lists the closest tags and says to use `grepwise add <url> --ref <tag>`. Indexing the wrong version would break the core promise.
4. **Overrides:** `known_sources.toml` (built in, plus `GREPWISE_HOME/known_sources.toml`, which wins) can set `repo` and a `tag` pattern per package, e.g. SQLAlchemy's `rel_{version_underscored}`.
5. **Cache:** successful resolutions go in a `resolutions` table (no network on the next lookup). `--refresh` ignores it.

Only GitHub repos are supported; anything else is reported as unresolved with the URL found.

## 5. Storage changes (schema step 2)

- `sources.ref`: the git ref the files came from (tag or commit); used for links in N3.
- `sources.package`: the PyPI name, for sources added by package. Package sources are named after the package (`typer@0.27.2`), so citations match the lockfile.
- `resolutions (ecosystem, package, version, repo_url, tag, resolved_at)`.

## 6. Commands

- **`grepwise add <target>`**, where target is:
  - `typer`: the version from the current project, or the latest on PyPI (and says so);
  - `typer==0.27.2`: that version;
  - `https://github.com/owner/repo --ref <tag>`: a repo, named `owner/repo`;
  - `./path` or `~/code/lib`: a local folder, named after the folder (or `--name`), versioned by its git commit (`--version` to override; `local` if not a git repo). Read in place, no copy.
  Prints what it resolved (repo, tag), then the usual index summary. Re-adding the same version re-indexes in place.
- **`grepwise deps`**: the current project's dependencies: name, version, direct or transitive, where the version came from, and whether it's indexed.
- **`grepwise sync [--all] [--dry-run]`**: add every direct dependency (`--all`: transitive too) that isn't indexed at its locked version. One failure doesn't stop the rest; ends with a table of added / already indexed / failed (with the reason). `--dry-run` only resolves.
- **`grepwise remove <name@version> [--type ...] [--yes]`**: deletes a source with its documents, chunks and keyword rows; embeddings are kept. Asks first unless `--yes`; needs `--type` when a name and version match several types.

## 7. Eval sources

`make demo-sources` indexes Typer and Click as one repo source each (their docs come along), so remove the old separate docs sources. The questions don't change. Hybrid Hit@5 must stay ≥ 0.85.

## 8. Acceptance

- [ ] Unit tests (offline, with fake PyPI and fake tag lists): reST headings (styles, overlines, levels, literal blocks, tables); project reading from `uv.lock`, `poetry.lock`, `requirements.txt` and a fake `.venv`; GitHub URL extraction from real-shaped PyPI JSON; tag matching for each pattern, including a known_sources override; the "no matching tag" error lists candidates; search deduplicates citations; `remove`.
- [ ] Live: `grepwise deps` and `grepwise sync --dry-run` in this repo's `backend/` resolve every direct dependency (or explain why not); `grepwise add` works for a package name, a `name==version`, a GitHub URL and a local folder.
- [ ] Live: a reST-documented package (e.g. `requests`) gets section chunks with sensible headings.
- [ ] `make eval-retrieval` after re-indexing the eval sources: hybrid Hit@5 ≥ 0.85.
- [ ] `make test` and `make lint` pass.

## 9. Results (2026-10-10)

| Check | Result |
|---|---|
| Tests | 290 passed, offline; ruff and mypy clean |
| Resolution, 20 real packages | 20/20 correct repo and tag (bare and `v` tags, repos named differently from the package, SQLAlchemy's `rel_2_1_4` via known_sources.toml) |
| `sync --dry-run` in `backend/` | 14/16 direct dependencies resolved; `types-jsonschema` and `types-pyyaml` fail correctly: typeshed has no release tags |
| `add requests==2.34.2` | 961 chunks, 314 reST sections with correct heading paths; "timeout" search finds both Timeouts sections first |
| `add <local folder>` | Grepwise itself, named `grepwise@<commit>` |
| `add pyyaml` | Version from `uv.lock`, lookup from the cache |
| Re-adding Typer as one source | 8 s (3,428 embeddings reused, 38 created) instead of ~4 min |
| Retrieval, hybrid | Hit@5 **0.88**, MRR 0.74 (target ≥ 0.85) |
| Retrieval, keyword / vector | 0.84 / 0.84 (both up from 0.82 / 0.80) |

Hybrid Hit@5 went 0.90 → 0.88: code-13 is now found; docs-15 (Click's `utils.py`, which implements `LazyFile` and `atomic`, now outranks the docs page) and cross-06 (a general environment-variables page outranks the exceptions page, which no longer gets two duplicate slots) dropped out of the top 5.

## 10. As built

- Commands live in `grepwise/commands.py`; dependency reading in `grepwise/project.py`; resolution in `grepwise/resolve.py` with `known_sources.toml` next to it.
- The "closest tags" hint ranks tags by numeric distance (major, then minor, then patch), not by text similarity, which suggested `v1.0` for `3.0` when `v2.9` existed.
- Until N4, run the commands with `uv run --project <grepwise>/backend ...` from your project: `--directory` changes into `backend/`.
