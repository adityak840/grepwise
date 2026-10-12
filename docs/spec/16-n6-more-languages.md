# N6: More languages

Part of [10-v2-plan.md](10-v2-plan.md). Written 2026-10-12, after two real sessions hit the limit: a recharts question in a JS/TS `ui/` folder (no project found), and a Playwright question whose answer lives in an npm package. **Goal:** Grepwise works the same way for JavaScript/TypeScript, Go and Rust projects as for Python: the exact locked version, indexed on first use, cited and linked.

**Your decision (2026-10-12):** lift "Python only". Python stays the reference; each new ecosystem must be measured before it counts as supported.

Terms used below:
- **Ecosystem:** a language's package world: its manifest file, lockfile and registry. PyPI for Python, npm for JS/TS, Go modules, crates.io for Rust.
- **Manifest:** the file where a project declares what it depends on (`pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`).
- **Lockfile:** the file recording the exact version installed for every package (`uv.lock`, `package-lock.json`, `go.sum`/`go.mod`, `Cargo.lock`).
- **Registry:** the server that hosts packages and their metadata (pypi.org, registry.npmjs.org, crates.io).
- **Monorepo:** one git repo holding many packages, each in its own folder (Playwright, Babel, most Rust workspaces).

## 1. What changes and what doesn't

Everything after "which files, from which repo, at which tag" is already language-neutral.

| Part | Change |
|---|---|
| Storage, keyword and vector search, fusion, citations, links, background embedding, MCP server, `read` | **None** |
| Markdown, MDX and reST docs chunking | **None** |
| Code chunking (tree-sitter) | JS/TS/TSX already done. **Add Go and Rust** grammars and their definition types. |
| Reading the project | **Per ecosystem** (section 3). One folder can hold several projects. |
| Package → repo → tag | **Per ecosystem** for the registry lookup and tag habits; the cache, `git ls-remote`, matching and "no guessing" stay shared. |
| Indexing a subfolder | **New:** monorepo packages index their own folder plus the repo's docs, not the whole repo. |
| Names | **New:** the same name can exist in two ecosystems (`requests` is on PyPI and npm). |

## 2. The ecosystem interface (N6a: refactor, no new behavior)

A package `grepwise/ecosystems/` with one module per ecosystem. `project.py` and the PyPI parts of `resolve.py` move into `ecosystems/pypi.py`; the rest becomes shared.

Each ecosystem provides:

| Member | What it does | PyPI example |
|---|---|---|
| `name` | short id, stored in the database | `"pypi"` |
| `markers` | files that mark a project root | `pyproject.toml`, `uv.lock`, `poetry.lock`, `setup.py`, `requirements*.txt` |
| `read(root)` | own name, dependencies (name, version, direct or not, where the version came from, from the registry or not) | today's `read_project` |
| `normalize(name)` | canonical name | PEP 503 |
| `registry_info(name, version)` | GitHub repo URL and, for monorepos, the package's folder; `None` if the registry has no such package or version | PyPI JSON `project_urls` |
| `tag_candidates(name, version)` | tags to try, in order | `v1.2.3`, `1.2.3`, `name-1.2.3`… |

Shared (`resolve.py`): the `resolutions` cache (it already has an `ecosystem` column), `known_sources.toml`, `git ls-remote`, picking the first matching tag, and the "no tag matched, closest are …" error.

`Dependency.from_pypi` becomes `from_registry`, and `Dependency` gains `ecosystem`.

**Done when:** no behavior change. All tests pass unchanged except imports, the resolution eval is still 33/33, and retrieval Hit@5 is unchanged.

## 3. Finding the projects in a folder

Today: walk up from the folder Claude Code is in to the first folder with a Python marker. Your repos have a Python `backend/` and a JS `ui/` side by side, often opened at the repo root, which has neither.

New rule, given the starting folder (the `project` argument, or the working directory):

1. **Walk up** (same stop below the home folder as today) to the nearest folder that has any ecosystem's markers.
2. **Also look one level down** from the starting folder: immediate subfolders with markers, skipping hidden folders and the usual junk (`node_modules`, `vendor`, `target`…), at most 20.
3. Every folder found is read by every ecosystem whose markers it has, and the dependencies are merged.

Examples: opened at the repo root, you get `backend/` (pypi) and `ui/` (npm); opened in `ui/src/`, just `ui/`; opened in `backend/app/`, just `backend/`.

`libraries` reports `projects: [{root, ecosystems}]` instead of one `project` path, and each dependency carries its `ecosystem`.

## 4. JavaScript and TypeScript (N6b)

**Project:** marker `package.json`.
- **Declared:** `dependencies`, `devDependencies`, `optionalDependencies`, `peerDependencies`.
- **Versions, first that has the package:**
  1. `package-lock.json` (v2/v3): `packages["node_modules/<name>"].version`. Only the top-level entry: nested `node_modules/a/node_modules/<name>` entries are other packages' private copies.
  2. `pnpm-lock.yaml`: `importers["<folder>"].dependencies|devDependencies[<name>].version`, without the peer suffix (`18.3.1(react-dom@18.3.1)` → `18.3.1`).
  3. `yarn.lock`: v1's own format (`"name@^1.0.0":` then `version "1.2.3"`), or Berry's YAML.
  4. Installed: `node_modules/<name>/package.json`.
- **Workspaces** (a JS monorepo of your own): the lockfile sits at the workspace root, not next to each `package.json`. If a root has no lockfile, use the nearest one above it, and its entry for this folder (pnpm `importers`).
- **Not from the registry:** versions starting `file:`, `link:`, `workspace:`, `git+`, `github:` or `http`.
- **Names:** scoped names keep their scope (`@tanstack/react-query`). The plain-name match that lets `typer` find `fastapi/typer` must not let `node` find `@types/node`: it applies only to sources added by URL.

**Registry:** `https://registry.npmjs.org/<name>/<version>` (a scoped name is URL-encoded: `@scope%2Fname`).
- `repository` comes as a string (`"github:owner/repo"`, `"owner/repo"`, a URL) or an object `{url, directory}`. Normalize `git+https://github.com/owner/repo.git`, `git://`, `git+ssh://git@github.com/…` to `https://github.com/owner/repo`.
- Fallbacks: `homepage`, then `bugs.url`, if they are GitHub URLs.
- **`@types/*`** packages all live in DefinitelyTyped, which has no per-package tags. Report them as "type definitions: not indexed" (like typeshed today), not as failures.

**Tags**, in order: `v<v>`, `<v>`, `<name>@<v>` with the full scoped name (`@scope/pkg@1.2.3`, the changesets convention), `<unscoped>@<v>`, `<unscoped>-v<v>`, `<unscoped>-<v>`.

**Monorepo packages** (playwright-core, most of `@babel/*`, `@radix-ui/*`):
- Folder: `repository.directory` if given. Otherwise, after cloning, the folder under `packages/` whose `package.json` has this package's name. Otherwise the whole repo, with a note.
- Index that folder plus the repo's root `README*` and docs folders (`docs/`, `doc/`, `website/docs/`). Paths stay relative to the repo root, so links keep working. The folder goes in `sources.subpath`.
- Clone sparsely (`git clone --depth 1 --filter=blob:none --sparse`, then `git sparse-checkout set <folder> docs …`) so `microsoft/playwright` doesn't download everything. Measure it; fall back to the normal shallow clone if a server doesn't support it.

**Note:** npm ships built JavaScript, but the tag has the original TypeScript source. Grepwise cites the source, which is what you want to read; the line numbers won't match files in `node_modules/…/dist`.

## 5. Go (N6c)

**Project:** marker `go.mod`. Its `require` lines are already exact versions (Go picks one version per module): `// indirect` means transitive. `replace` lines pointing at a local path mean "not from the registry".

**No registry needed:** the module path *is* the repo, and the version *is* the tag.
- `github.com/owner/repo` → that repo, tag `v1.2.3`.
- A module in a subfolder (`github.com/owner/repo/sub`) → folder `sub`, tag `sub/v1.2.3`.
- A major-version suffix (`…/repo/v2`) is usually not a folder: drop it if there's no `v2/` folder.
- `+incompatible` is dropped from the tag.
- **Pseudo-versions** (`v0.0.0-20240101120000-abcdef123456`, a commit without a tag): the last part is the commit, which becomes the ref. Clone by commit (GitHub allows a shallow fetch of one commit).
- **Vanity paths** (`golang.org/x/…`, `gopkg.in/…`, `k8s.io/…`): fetch `https://<path>?go-get=1` and read the `go-import` meta tag. Supported when it points at GitHub (`golang.org/x/*` is mirrored at `github.com/golang/*`); otherwise unresolved, with a `known_sources.toml` hint.

**Chunking:** `function_declaration` → function, `method_declaration` → method, `type_declaration` → class. Files ending `_test.go` count as tests for embedding order.

## 6. Rust (N6d)

**Project:** marker `Cargo.toml`.
- **Declared:** `[dependencies]`, `[dev-dependencies]`, `[build-dependencies]`, plus `[workspace.dependencies]` and each member's tables for a workspace.
- **Versions:** `Cargo.lock` packages whose `source` starts `registry+`; `git+` sources are "not from the registry".

**Registry:** `https://crates.io/api/v1/crates/<name>/<version>` gives `repository`. crates.io requires a descriptive `User-Agent` header (`grepwise (+https://github.com/adityak840/grepwise)`) and allows about one request a second, so lookups go through the cache like everything else.

**Tags:** `v<v>`, `<v>`, `<name>-v<v>`, `<name>-<v>`, `<name>@<v>`.

**Workspace crates** (most big Rust projects, e.g. `tokio`): crates.io has no folder field, so after cloning, find the folder whose `Cargo.toml` has `name = "<crate>"`. Index it plus the root docs, as for npm monorepos.

**Chunking:** `function_item` → function; `impl_item` and `trait_item` → class, with their `function_item`s as methods when large; `struct_item` and `enum_item` → class.

## 7. Storage (schema step 5)

- `sources.ecosystem`: `pypi`, `npm`, `go` or `cargo` for sources added by package; NULL for URL, local-folder and OpenAPI sources. Existing package sources become `pypi`.
- **The uniqueness rule must include the ecosystem.** Today `UNIQUE (type, name, version)` would let an npm package overwrite a PyPI package with the same name and version. SQLite can't change a constraint in place, so step 5 rebuilds `sources` (create the new table, copy, drop, rename, with foreign keys switched off for the copy), with a unique index on `(type, coalesce(ecosystem, ''), name, version)`. A test checks that documents, chunks and embeddings survive the migration.
- Lookups by package (`dependency_sources`, `_index_dependency`, the "being indexed" check) filter by ecosystem too.

## 8. Tools and commands

**Library names** in `search`, `read` and `libraries`:
- A plain name means that package in any of the project's ecosystems. Both are searched if the project uses the name in two (you depend on both).
- A prefix makes it explicit: `npm:recharts`, `pypi:requests`, `go:github.com/spf13/cobra`, `cargo:serde`. Versions as today: `npm:recharts@2.15.0`.

**CLI:**
- `grepwise add npm:recharts`, `npm:recharts@2.15.0`, `go:github.com/spf13/cobra@v1.8.0`, `cargo:serde@1.0.200`.
- A bare `add typer` / `typer==0.27.2` stays PyPI, so nothing you already type changes.
- `deps` and `sync` cover every project found in the folder (section 3), with an ecosystem column.

**Hints** (`add_hint`, added 2026-10-12) check the registry of the ecosystem(s) the project uses and suggest the prefixed form, e.g. "`grepwise add npm:recharts`".

**Server instructions** don't change: they already say "this project's dependencies".

## 9. `known_sources.toml`

Per-ecosystem tables, e.g. `[npm."@scope/pkg"]` or `[cargo.foo]`, with the same `repo` and `tag` keys plus a new `subpath`. Existing top-level tables (`[sqlalchemy]`) keep meaning PyPI.

## 10. Evals

- **Resolution:** `evals/resolution.yaml` gains an `ecosystem` field. Add 30 npm packages (including recharts, react, next, vite, zod, typescript, express, axios, @tanstack/react-query, playwright-core and at least five monorepo packages), 15 Go modules (including a vanity path and a subfolder module) and 15 Rust crates (including workspace crates such as tokio). Pin each at a fixed version and write the expected repo, tag and folder by hand. **Target:** at least 90% correct per ecosystem, and every failure has a clear message (no tags, not on GitHub).
- **Retrieval:** 10 questions per new ecosystem on two libraries each (npm: recharts, zod; Go: cobra; Rust: clap), in `evals/questions.yaml` like the Python ones. **Target:** hybrid Hit@5 ≥ 0.80 per ecosystem; the Python questions stay ≥ 0.85.
- **Answers eval** (Claude Code with vs. without; uses your plan's usage): optional, a small trial only, and only after you've checked the cost.

## 11. Order and effort

| Step | What | Effort | Done when |
|---|---|---|---|
| **N6a** | Ecosystem interface, project discovery in sections 3 and 7, schema step 5 | small–medium | no behavior change for Python; mixed folders found |
| **N6b** | npm, including monorepo subfolders and sparse clones | medium (about N2) | your `ui/` folder answers the recharts question; npm resolution ≥ 90% |
| **N6c** | Go | small | Go resolution ≥ 90% |
| **N6d** | Rust | small–medium | Rust resolution ≥ 90% |

Each step ends with tests passing, a code review and a quiz, as before. N6b is the one that matters most to you; N6c and N6d can wait until you work in Go or Rust.

## 12. Not in scope

- Other ecosystems (Java/Maven, Ruby, PHP, .NET, Swift). The interface allows them later; Maven's metadata rarely links a repo reliably, so each would need its own look.
- Repos not on GitHub (GitLab, Bitbucket, Gerrit) and private registries.
- **Docs in a separate repo.** Common in JS: React's docs live in `reactjs/react.dev`, recharts' in a site repo. These repos usually have no version tags, so they can't be matched to your version. Left for a later step: a `docs` key in `known_sources.toml`, indexed at the default branch and clearly labelled "not version-matched".
- **Wrapper packages** whose logic lives in another ecosystem (Python `playwright` → npm `playwright-core`). N6b makes the second half indexable (`grepwise add npm:playwright-core@1.61.0`); linking the two automatically is a later `known_sources.toml` feature.

## 13. Acceptance

- [ ] N6a: all existing tests pass; resolution eval 33/33; retrieval unchanged; schema step 5 migration tested on a copy of a real database; a folder with `backend/` (Python) and `ui/` (npm markers) lists both projects.
- [ ] N6b, offline unit tests (fake registry, fake tag lists, fixture lockfiles): each lockfile format, including nested `node_modules` entries, pnpm peer suffixes and yarn v1; workspaces; every `repository` shape; scoped tags; monorepo folder detection with and without `directory`; `@types/*`; `@types/node` not matching `node`.
- [ ] N6b, live: your `ui/` folder lists recharts at its locked version, and "how do I create a line chart" returns cited results from the recharts tag; `playwright-core@1.61.0` indexes only its package folder plus docs, and the sparse clone is measured.
- [ ] N6c and N6d: offline tests for parsing, path/tag rules, pseudo-versions, vanity paths, workspace crates and the new grammars; resolution sets at ≥ 90%.
- [ ] Retrieval targets in section 10 met; README, CLAUDE.md and the v2 plan's scope updated.
- [ ] `make test` and `make lint` pass.

## 14. N6a as built (2026-10-12)

- `grepwise/ecosystems/`: `__init__.py` holds the shared types (`Dependency` with `ecosystem` and `from_registry`, `Project` with `roots`, `RegistryInfo`, the `Ecosystem` base class), project discovery and the shared tag patterns. `pypi.py` is the old `project.py` plus the PyPI lookup. `resolve.py` is generic: `Resolver(conn, ecosystem=...)`.
- **Looking one level down** happens only when the starting folder is a project or a git repo root. Opening Claude Code in a plain folder of repos (`~/Documents`) would otherwise merge all of them.
- **`sources.ecosystem` is `''`, not NULL,** for sources that aren't packages, so the uniqueness rule can be a plain `UNIQUE (type, ecosystem, name, version)`. Step 5 rebuilds the table with foreign keys off. A step marks this with `-- needs: foreign_keys off`; the migration checks `foreign_key_check` before committing. On a copy of the real database: 11 sources, 50,566 chunks and 44,424 embeddings kept, in 0.5 s.
- **MCP servers started before an upgrade keep running the old code.** Their package rows (no ecosystem) are repaired to `pypi` on the next connection. But their indexing fails against the new uniqueness rule, so **restart Claude Code after `make install`**.
- **Fixed on the way:**
  - `~/.grepwise/known_sources.toml` was never loaded by the resolver, though messages told users to add entries there. It is now loaded; a broken file is skipped with a warning instead of stopping resolution.
  - Naming the locked version (`httpx@0.28.1`) now indexes on demand like the plain name.
  - The `owner/repo` short-name match only applies to sources added by URL.
- **Left as known tradeoffs:**
  - Semantic search is off while any source being searched is under 90% embedded. A large dependency like numpy can make default searches keyword-only for about an hour.
  - The PyPI check in hints, like on-demand indexing, runs while the session's tool lock is held.
  - The embedding-progress note runs a count per source on each search.

## 15. N6b as built (2026-10-12)

- `ecosystems/npm.py`: package.json; package-lock.json v1–v3 (only top-level `node_modules/<name>`, a workspace member's own entry first), pnpm-lock.yaml (importer per folder, peer suffix dropped), yarn.lock classic and Berry, then `node_modules`. Workspace lockfiles are found above the package. The registry's `repository` (string, shorthand or `{url, directory}`), then homepage and bugs. `@types/*` is refused with a reason.
- **Monorepo packages:** `index_repo(subpath=..., locate=...)`. The folder comes from `repository.directory`, else from the `package.json` with that name in the clone (4 levels deep). It is indexed with the repo's README and `docs/`, `doc/`, `website/docs/`, and stored in `sources.subpath`. Sparse clones weren't needed: `microsoft/playwright` at v1.61.0 cloned and indexed `packages/playwright-core` (585 files, 9,446 chunks) in 18 s.
- **Names:** scoped names may start with `@` in source names, refs, tags and citations (`split_ref` everywhere). `npm:` / `pypi:` prefixes are accepted in the tools and in `grepwise add`. A plain name present in two ecosystems is searched in both. Hints check the project's registries.
- **Live:** 18/20 popular npm packages resolved to the right repo and tag (6 with a monorepo folder). The two failures: `@types/node` (by design), and `@radix-ui/react-dialog`, whose repo has no per-package tags (the closest tags are reported). A real recharts 2.15.0 project: the first search indexed 1,745 chunks in 7.5 s and returned the Getting Started docs, a demo chart and `XAxis.tsx`.
- Not done yet: a resolution eval file for npm (section 10), retrieval questions for JS, Go (N6c), Rust (N6d).
- **Found in real use (2026-10-12):** ag-grid's repo has a stale `scripts/package.json` that is also named `ag-grid-community` (at 31.0.0), so the first match indexed build scripts. The folder whose `package.json` is at the version being indexed now wins, then the shallowest. Re-indexed: `packages/ag-grid-community`, 774 files, with pagination questions landing in `src/pagination/`.
