# Grepwise v2: focus on the one job

Written 2026-10-10. **Agreed** the same day; this replaces the remaining M6 plan (phases 6.2–6.4).

**Your decisions:** SQLite instead of Postgres (you already use Postgres at work); archive the built-in agent, HTTP API and web UI at a git tag; Python only for now.

## 1. The job

**When I'm coding against a library, I want my assistant to read that library's real docs and source, at the exact version my project uses, so that I stop getting answers based on outdated memory, and I can click through to the lines it's based on.**

The user is you, in Claude Code, every day. The test of success is simple: you keep it connected because it saves you time, the way your file transfer app does.

## 2. Where the friction is today

To ask "how do I make an option required?" about Typer in a new project, you currently have to:

1. Start Docker Desktop (if it isn't running, the MCP server fails to start).
2. `cd` into the Grepwise checkout.
3. Look up which Typer version your project uses.
4. Look up its git tag and where its docs live.
5. `uv run grepwise index-docs https://github.com/fastapi/typer --ref 0.27.2 --path docs`, then `index` for the code, and wait about 4 minutes.
6. Have registered the MCP server with an absolute path to the checkout.
7. Ask, and hope the model remembers Grepwise exists instead of answering from memory.

Every step is a reason to give up and just ask the model directly. **Version-exact sourced answers are the value. Steps 1–6 are the cost.** v2 removes the cost.

## 3. What "worth using" means (measurable)

| Goal | Target |
|---|---|
| Setup | One install command and one `claude mcp add`. No Docker, no API keys, works in every project. |
| Indexing | None by hand for your dependencies. The first question about a library works within seconds (keyword search), and fully (semantic search too) within a few minutes. |
| Version | The version in your lockfile, not "latest". |
| Trust | Every result has a citation and a clickable GitHub link to the exact lines. |
| Proof | Claude Code **with** Grepwise beats Claude Code **without** it on the eval set (citation hits, fact coverage, honesty on unanswerable questions). If it doesn't, we find out why before building more. |

## 4. What exists already, and why Grepwise is still worth building

Hosted MCP servers already serve library docs (e.g. Context7) or answer questions about public GitHub repos (e.g. DeepWiki). Grepwise has to be better on something real, not just exist. Its edges:

- **Your exact versions**, read from your lockfile.
- **Docs and source together**: docs explain intent, source settles what actually happens.
- **Local and private**: works offline, and on internal repos from a local folder, which hosted services can't see.
- **Line-level citations with links**, so checking an answer takes one click.

## 5. Decisions: keep, change, drop

| Area | Decision | Why |
|---|---|---|
| Chunkers (tree-sitter, Markdown, OpenAPI), fetch, filters, citations | **Keep** | The core, and they work. |
| Hybrid search, local embeddings | **Keep, then measure** (section 8) | If keyword search alone is nearly as good, dropping embeddings makes indexing near-instant and removes a 290 MB model. |
| MCP over stdio | **Keep, as the product** | It's how you'll use it. |
| Postgres in Docker | **Replace with SQLite** (one file, FTS5 for keywords, `sqlite-vec` for vectors) | No server to start, nothing to break when Docker is off, one index shared across all your projects. |
| Built-in agent (Gemini/Ollama), HTTP API, SSE, rate limits, visitor keys, MCP over HTTP, `agent_runs`, web UI | **Archive at a git tag, remove from main** | Over MCP, the editor's model does the reasoning. These add install weight, API-key setup and maintenance (every storage change must keep them working) for something you won't use daily. The tag keeps the work visible. |
| Five tools + `verify_citation` | **Redesign as three** (section 6) | Fewer, smarter tools mean fewer wrong calls and less context spent on tool definitions. |
| Evals | **Keep, retarget** | Retrieval quality, Claude Code with vs. without, and auto-resolution accuracy. |

## 6. How v2 works

**Install once:**
```bash
uv tool install <grepwise>                         # puts `grepwise` on your PATH
claude mcp add --scope user grepwise -- grepwise mcp   # every project, no paths
```

**Ask in any project.** The flow behind one question:

1. Claude Code calls `search(query="make an option required", libraries=["typer"])`.
2. Grepwise finds the project root (MCP roots if the client sends them, otherwise the working directory Claude Code started it in) and reads the lockfile: `typer==0.27.2`.
3. If `typer@0.27.2` isn't indexed yet, it **resolves** it: PyPI's JSON API gives the GitHub repo (from the project URLs), `git ls-remote` finds the tag (`v0.27.2`, `0.27.2`, `typer@0.27.2`…), and a docs folder is detected (`docs/`, `doc/`, `website/docs`…).
4. It does a **shallow, sparse clone** (only that tag, only the package folder and docs), chunks everything, and builds the **keyword index in seconds**. It answers right away from keyword search and says semantic search is warming up.
5. **Embeddings fill in the background**: docs first, then package source, then examples, then tests. Later searches use hybrid search. Progress survives restarts.
6. Results come back as snippets, each with a citation and a link like `https://github.com/fastapi/typer/blob/0.27.2/docs/…#L12-L30`, which you can click in the terminal.

**The three tools:**

| Tool | What it does |
|---|---|
| `libraries()` | This project's dependencies with versions and index state, plus sources you added by hand. |
| `search(query, libraries?, kind?)` | Hybrid search across the named libraries (plain names, resolved to your versions) or all of the project's indexed ones. Indexes on demand. Exact symbol names rank high, so `find_symbol` isn't needed. |
| `read(citation \| library + path + lines)` | Exact lines with line numbers and a link, to expand a result. |

**Server instructions** (sent to the client by MCP) tell the model when to use Grepwise: before writing or explaining code that uses a third-party library, search it at the project's version instead of relying on memory.

**CLI** (for what doesn't belong in a tool): `grepwise add <name | GitHub URL | local path | OpenAPI URL>`, `grepwise sync` (pre-index all of a project's dependencies), `grepwise sources`, `grepwise remove`, `grepwise gc` (delete sources unused for 30 days), `grepwise doctor`, `grepwise setup` (runs the `claude mcp add` for you).

## 7. Scope

- **Ecosystem: Python only** (`uv.lock`, `poetry.lock`, pinned `requirements*.txt`, else the installed `.venv`). The resolver is written so another ecosystem (JS/TS lockfiles and the npm registry) can be added later as one more module.
- **Docs formats:** Markdown and MDX (done), plus **reStructuredText**, since many Python libraries (Django, SQLAlchemy, requests, NumPy) write Sphinx docs.
- **Docs in a separate repo** (rare for Python, but it happens): a small `known_sources.toml` mapping package → docs repo, easy to extend.
- **Local paths:** `grepwise add ~/code/internal-lib` indexes a folder at its current commit; links point to the local file.
- **Not in scope:** hosting, accounts, a chat UI, other editors' special features (any MCP client works), private GitHub auth (use a local clone).

## 8. Measured (N0, done 2026-10-10)

Share of the 51 answerable eval questions with an expected file in the top 5 results (Hit@5):

| Mode | Hit@5 | MRR |
|---|---|---|
| Postgres keyword (v1) | 0.35 | 0.23 |
| nomic vector (v1) | 0.80 | 0.73 |
| Postgres hybrid (v1) | 0.78 | 0.62 |
| SQLite FTS5 keyword, with the file path | 0.82 | 0.71 |
| **FTS5 + nomic (v2)** | **0.88** | **0.74** |
| bge-small vector | 0.78 | 0.67 |

What it decided:
- **Keep hybrid search and keep `nomic-embed-text-v1.5`.** Vectors add 6 points over the best keyword search; the smaller model is only 1.6× faster and less accurate.
- **SQLite's FTS5 is better than what we had,** because its BM25 ranking gives rare words more weight. The move to SQLite improves quality as well as removing friction.
- **Keyword-only results are good enough to answer straight away** while embeddings fill in, so on-demand indexing (N3) is realistic.
- Code questions are the weakest category (0.70 at best). Look at the misses in N3.

## 9. Milestones

| # | What | Rough effort |
|---|---|---|
| **N0** | Measure (done, section 8) | done |
| **N1** | Slim down: archive tag, remove the agent, API and web UI; SQLite storage; retrieval eval runner. Spec: [11-n1-slim-sqlite.md](11-n1-slim-sqlite.md) | 1.5 days |
| **N2** | Project awareness: lockfile parsing, package → repo → tag → docs resolution, `grepwise add` (name, URL, local path), `known_sources.toml`, RST docs | 1.5 days |
| **N3** | The new MCP surface: three tools, on-demand indexing, background embeddings, links, server instructions | 1.5 days |
| **N4** | Install and onboarding: `uv tool install`, `grepwise setup`, `doctor`, `gc` | 0.5 day |
| **N5** | Prove it: Claude Code with vs. without (headless `claude -p`), a resolution eval on ~30 popular packages, README, demo video, case studies | 1.5 days |
| **Then** | You use it for a week of real work; we fix whatever annoys you; then the recap and quiz | |

Same process as before: a spec per milestone, built by the other session, reviewed here, then a short quiz.
