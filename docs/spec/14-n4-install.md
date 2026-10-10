# N4: Install and onboarding

Part of [10-v2-plan.md](10-v2-plan.md). **Goal:** two commands to go from nothing to working in every project:

```bash
uv tool install /path/to/grepwise/backend     # or from GitHub once the repo is public
grepwise setup
```

No editing config files by hand, no `uv run --project`, and a `doctor` command for when something's off.

## 1. Installable as a tool

- `uv tool install <path or git URL>` puts `grepwise` on the PATH in its own environment, with a uv-managed Python (which includes SQLite's FTS5).
- Package data travels with it: `db/schema*.sql` and `known_sources.toml` (already in the wheel).
- **Grammars download on first use:** if a tree-sitter grammar is missing when code is chunked, download it then (once per language per process), instead of silently falling back to plain windows. A download failure (offline) still falls back, with a warning.

## 2. `grepwise setup`

1. Creates `~/.grepwise/` and the database.
2. Downloads the embedding model and the tree-sitter grammars (Python, JavaScript, TypeScript, TSX), with progress.
3. Registers the MCP server for **every project**:
   - `--client claude-code` (default): `claude mcp add --scope user grepwise -- <command>` if the `claude` command exists; otherwise it writes the user-level `mcpServers.grepwise` entry in `~/.claude.json` itself, after saving a backup next to it.
   - `--client cursor`: `~/.cursor/mcp.json`. `--client claude-desktop`: `claude_desktop_config.json`. Both with a backup.
   - `--print`: only print the JSON or command, change nothing.
   - **The command registered is the absolute path of the running `grepwise`** (e.g. `~/.local/bin/grepwise mcp`, or `<checkout>/backend/.venv/bin/grepwise mcp`). A virtual environment's scripts run without `uv` and keep the client's working folder, so the project is found.
   - An existing `grepwise` entry is replaced (and says so); entries for other servers are never touched.
4. Ends with what to do next: restart Claude Code, try a question, run `grepwise sync` in a project.

Safe to run again.

## 3. `grepwise doctor`

One line per check, ✓ / ! (warning) / ✗ (failure) with the fix:

| Check | Fix hint |
|---|---|
| SQLite has FTS5 | "Install with uv (its Python includes FTS5)" |
| Database opens, schema up to date | the error |
| Embedding model downloaded | `grepwise setup` |
| Grammars downloaded | `grepwise setup` |
| `git` on PATH | install git |
| PyPI reachable | "on-demand indexing needs the network" (warning only) |
| Registered with Claude Code (user level, and which command) | `grepwise setup` |
| Registered command still exists on disk | `grepwise setup` |
| Sources: how many, failed ones, how many still embedding | `grepwise add` / `remove` |
| Disk use of `~/.grepwise` | info |

Exits 1 if any check fails.

## 4. `grepwise gc`

- **Last use is recorded:** `search` and `read` stamp `sources.last_used_at` (schema step 4) on the sources they touch.
- `grepwise gc [--days 30] [--all] [--dry-run] [--yes]` removes **package sources** (the ones added by name, on demand or by `sync`) not used for `--days` (never used: counted from when they were indexed). `--all` includes sources added by URL or folder.
- Then deletes **orphaned embeddings** (no chunk uses that content hash any more) and runs `VACUUM` to give the space back. Prints what it removed and the size before and after.
- Lists what it would remove and asks first, unless `--yes`.

## 5. README

The install section becomes the two commands above. The `uv run --project` notes go.

## 6. Acceptance

- [ ] Tests: setup writes the Claude Code / Cursor / Desktop configs correctly (temp home), keeps other servers, makes a backup, replaces an old entry, and `--print` changes nothing; doctor reports a missing model and a missing registration; gc removes only stale package sources (and with `--all`, others), deletes orphaned embeddings, honours `--dry-run`; search and read stamp `last_used_at`; a missing grammar is downloaded on first use (fake downloader).
- [ ] Live: `uv tool install ./backend` into a clean tool environment; `grepwise setup --print` and `grepwise doctor` from the installed tool; the installed `grepwise mcp` answers over stdio with the project found from its working folder.
- [ ] `make test` and `make lint` pass.

## 7. Results (2026-10-10)

| Check | Result |
|---|---|
| Tests | 309 passed; ruff and mypy clean |
| Isolated `uv tool install` (scratch `UV_TOOL_DIR`, your PATH untouched) | `grepwise` runs; Python 3.12.12, SQLite 3.51 with FTS5 |
| Installed `setup --print` | Registers the absolute path of the installed `grepwise` with `mcp` |
| Installed `doctor` | All checks pass (after the fix below) |
| Installed `grepwise mcp` over stdio, started from the demo project | Finds the project from its working folder; search returns markupsafe results with GitHub links |
| `doctor` on your real setup | All ✓; confirms the user-level Claude Code entry with `--project` |

**Found during the live install:** `uv tool install` ignores `uv.lock` and resolved newer, untested versions (fastembed 0.9.0, tree-sitter-language-pack 1.21.1, pydantic 2.14, typer 0.27.3); `doctor` caught it because the grammar cache is per version. Fix: `make constraints` exports the locked versions to `backend/constraints.txt` (committed), `make install` installs with `--constraints`, and CI fails if the file drifts from `uv.lock`. Reinstalled: exactly the locked versions.

Also found: a missing grammar made code chunking fall back to plain windows silently; grammars now download on first use.
