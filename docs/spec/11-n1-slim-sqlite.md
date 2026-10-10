# N1: Slim down and move to SQLite

Part of [10-v2-plan.md](10-v2-plan.md). **Goal:** the same indexing, search, tools and MCP server, with **no Docker, no Postgres and no LLM**: one SQLite file, and tests that run anywhere. The tool surface stays the same in N1 (N3 redesigns it), so this milestone changes *how* things are stored, not *what* they do.

Rough effort: 1.5 days.

## 1. Why (the measurement behind it)

On the 51 answerable eval questions (5,464 chunks), share of questions with an expected file in the top 5 results (Hit@5), and MRR:

| Mode | Hit@5 | MRR |
|---|---|---|
| Postgres keyword (today) | 0.35 | 0.23 |
| nomic vector (today) | 0.80 | 0.73 |
| Postgres hybrid (today) | 0.78 | 0.62 |
| **SQLite FTS5 keyword, with the file path** | **0.82** | **0.71** |
| **FTS5 (with path) + nomic, RRF** | **0.88** | **0.74** |
| bge-small vector (1.6× faster to index) | 0.78 | 0.67 |

- Postgres's `ts_rank_cd` has no IDF (rare words don't count more than common ones), and the `simple` dictionary keeps words like "how" and "do", so keyword search is weak and even drags hybrid below vector-only. FTS5's **BM25** weighs rare words properly.
- Keyword search alone (0.82) is already useful, which is what makes "searchable in seconds, semantic later" (N3) work.
- **Keep `nomic-embed-text-v1.5-Q`**: bge-small is less accurate and not much faster.

The script is in the scratchpad; the proper runner is section 7.

## 2. Before starting

1. Commit any uncommitted work, then tag the current state: `git tag -a v0.1-webapp -m "Web UI, built-in agent and HTTP API, before the v2 refocus"`. Everything removed below stays reachable there.
2. Do all of N1 on a branch (`v2-sqlite`).

## 3. Remove

| Remove | Notes |
|---|---|
| `web/` | The whole Next.js app. |
| `backend/src/grepwise/agent/` | Move `MCP_PROMPT` (used by the MCP server's prompt) into `mcp/server.py` or `mcp/prompt.py` first. |
| `backend/src/grepwise/api/`, `mcp/http.py` | HTTP API, SSE, rate limits, MCP over HTTP. |
| `backend/migrations/`, `backend/src/grepwise/db/` | Replaced by section 4. |
| `backend/ollama/`, `docker-compose.yml` | |
| `GeminiEmbedder` and `EMBEDDER=gemini` | Local embeddings only; keep `FakeEmbedder` for tests. |
| CLI: `ask`, `serve`, `runs`, `db migrate` | The schema is created automatically (section 4). Keep `index`, `index-docs`, `index-openapi`, `search`, `sources`, `stats`, `symbol`, `read`, `endpoint`, `verify`, `tool`, `mcp`. |
| Settings | Everything for the LLM, agent, chat, CORS, rate limits, MCP HTTP, Gemini, `DATABASE_URL`, `POSTGRES_PORT`. |
| Dependencies | `fastapi`, `uvicorn`, `openai`, `google-genai`, `psycopg`, `psycopg-pool`, `pgvector`. Check `httpx` is still needed (fetching OpenAPI specs) before removing it. |
| Tests | `test_agent.py`, `test_api.py`, `test_api_web.py`, and the HTTP tests in `test_mcp.py`. |

`.env.example` shrinks to the few settings that remain (section 4), all optional.

## 4. Storage: one SQLite file

**Location:** `GREPWISE_HOME` (default `~/.grepwise/`) holds everything: `grepwise.db`, and the model cache moves to `~/.grepwise/models/`. One folder to find, back up or delete.

**Connection (`store/db.py`):**
- `sqlite3` from the standard library. On open: `PRAGMA journal_mode=WAL` (readers don't block the writer, and several Claude Code sessions can each run an MCP server on the same file), `busy_timeout=5000`, `foreign_keys=ON`, `synchronous=NORMAL`.
- **Schema created automatically** on first open from `store/schema.sql`, versioned with `PRAGMA user_version`. Future changes add numbered steps applied in order, so there is no `migrate` command to remember.
- On first open, check that FTS5 is available (create a throwaway `temp` FTS5 table). If not, fail with a clear message naming the Python build.
- The MCP server keeps one connection per process behind a lock, as `SharedConnection` does today (`check_same_thread=False`).
- **One read transaction per search** (`BEGIN` … `COMMIT`): in WAL mode that's a consistent snapshot, which replaces today's `REPEATABLE READ`.

**Tables** (same meaning as today unless noted):
- `sources` (`type`, `name`, `url`, `version`, `subpath`, `commit_sha`, `status`, `error`, timestamps; unique on `type, name, version`).
- `documents` (`source_id` → cascade, `path`, `language`, `line_count`, `content`).
- `chunks` (`document_id`, `source_id`, `kind`, `symbol`, **`symbol_short` computed in Python** at insert time (SQLite has no `regexp_replace`; same rules as migration 005), `start_line`, `end_line`, `header`, `content`, `content_hash`, `search_text`). Indexes on `source_id`, `symbol_short`, `lower(symbol)`, `lower(symbol_short)`.
- **`embeddings`** (`model_id`, `content_hash`, `vector` as a float16 BLOB; primary key on both). **This replaces both `chunks.embedding` and `embedding_cache`:** a chunk's vector is looked up by its `content_hash`. Identical chunks share one vector, re-indexing reuses everything already embedded, and a chunk with no vector yet simply isn't in vector results. N3's background embedding relies on that last point.
- **`chunks_fts`**, an FTS5 table with columns `path_words` (the path with `/ _ . -` turned into spaces), `symbol`, `body` (= `search_text`), `tokenize='porter unicode61'`, `rowid` = `chunks.id`. Keep it in sync explicitly in the store's insert and delete code; don't use triggers, so there's one place to read.
- Drop `jobs`, `agent_runs` and `byo_key`.

**Writing** (`replace_content`): one transaction deletes the source's chunks (and their `chunks_fts` rows) and documents, inserts the new ones, and marks the source `ready`. New embeddings are saved as they're made (separate small transactions), so a crash mid-index loses no embedding work, as today.

## 5. Search

**Keyword:** `chunks_fts MATCH` with the `query_terms` joined as `"t1" OR "t2" …` (each term double-quoted, so nothing in a question can be read as FTS5 syntax), ranked by `bm25(chunks_fts)` with equal column weights, filtered to the requested sources and kinds through a join on `chunks`, top `SEARCH_CANDIDATES`.

**Vector:** brute force in numpy, which is exact (no approximate index) and needs no native extension:
- Load the vectors for the filtered sources' chunks (join `chunks` → `embeddings` on `content_hash` and the current `model_id`) into a matrix `M` (one row per chunk) with a parallel array of chunk ids.
- `scores = M @ q` (vectors are normalized, so the dot product is cosine similarity), then `np.argpartition(-scores, k)[:k]` for the top `k = SEARCH_CANDIDATES`, then sort only those `k` by score. Guard `k >= N` (just sort everything).
- **float16 on disk, float32 in memory.** numpy only uses the fast BLAS routines for float32: measured over 20,000 × 768, a float16 matrix takes 23.8 ms per query and a float32 one takes 0.8 ms. Convert once, when the matrix is cached.
- **Cache per source** in the process: `{source_id: (updated_at, chunk_ids, matrix)}`, rebuilt when `updated_at` changes. Typer's code is about 3,100 × 768 × 4 bytes ≈ 9.5 MB in memory, so dozens of libraries fit easily. Searching several sources stacks their cached matrices (`np.vstack`), or scores each one and merges the top `k`, whichever is simpler.
- Target: under 5 ms for 20,000 chunks once the cache is warm (the cold first load from SQLite is allowed to take longer).

**Hybrid:** RRF with k = 60, unchanged (`rrf_merge`), then the same token budget and result shape as today.

## 6. Tools, CLI and MCP

- Port every SQL statement in `tools/` (`sources`, `read_file`, `find_symbol`, `get_endpoint`, `verify`) to SQLite. Behaviour and JSON output stay the same, so the existing tool tests should pass with only fixture changes.
- `grepwise stats` reports the database file's size instead of `pg_database_size`.
- The MCP stdio server works exactly as before (same tools, resources and prompt), now with no database to start.

## 7. Retrieval eval runner (`grepwise eval retrieval`)

- `grepwise eval retrieval [--mode hybrid|keyword|vector] [--questions evals/questions.yaml] [--include-drafts]`.
- For each answerable question: run search with that mode, restricted to the question's `sources`, using `SEARCH_CANDIDATES`. **Hit@5:** an expected path (`paths`, or any of `paths_all`) among the top 5 results' paths. **MRR:** 1 / the rank of the first result with an expected path (0 if none).
- Prints a table (overall and per category) and writes `evals/results/retrieval-<mode>.json` with per-question ranks, so changes can be compared question by question.
- Search gets a `mode` parameter for this (default `hybrid`); the tools always use hybrid.
- `make eval-retrieval` runs all three modes. Offline, no LLM.

## 8. Tests and tooling

- **conftest:** each test gets a fresh database in a temp folder (`tmp_path`); no Docker, no `GREPWISE_REQUIRE_DB`, nothing skipped.
- New tests: schema creation and `user_version`; `replace_content` keeps `chunks_fts` in sync (old chunks unfindable after a re-index); a chunk without a vector still appears in keyword results; FTS5 query building can't be broken by quotes, `*`, `NEAR` or `-` in a question; the vector cache is rebuilt after a re-index; WAL lets a reader search while another connection writes.
- **Makefile:** `setup` (uv sync, grammars, embedding model), `test`, `lint`, `fmt`, `eval-retrieval`, and `demo-sources` (indexes the eval sources: itsdangerous 2.2.0; Typer 0.27.2 docs and code; Click 8.5.0 docs and code; Petstore 1.0.27). No `db`, `migrate`, `serve` or `ollama`.
- **CI** (`.github/workflows/ci.yml`): `uv sync`, lint, tests. No service container.
- README: rewrite Local setup and Usage for the new commands. A full README rewrite comes in N5, so keep this short and correct.

## 9. Acceptance

- [ ] `v0.1-webapp` tag exists; `web/`, the agent, the API, Postgres and Docker are gone from the branch; no import of removed modules remains (`grep -r "psycopg\|fastapi\|openai\|grepwise.agent\|grepwise.api"` is empty).
- [ ] `make test` and `make lint` pass with Docker stopped.
- [ ] `make demo-sources` indexes all six sources from scratch into `~/.grepwise/grepwise.db`.
- [ ] `make eval-retrieval`: **keyword Hit@5 ≥ 0.80 and hybrid Hit@5 ≥ 0.85** (the measured values were 0.82 and 0.88; small differences in tokenization are fine, big drops are bugs).
- [ ] Re-indexing an already indexed source reuses every embedding (the summary shows 0 created).
- [ ] Claude Code, connected with `grepwise mcp`, answers a Typer question with valid citations, with Docker not running.
- [ ] Vector search over all six sources takes under 5 ms once the cache is warm (print the timing in `grepwise search --verbose`).

## 10. As built (differences from the plan above)

- **Module layout:** the connection and schema stay in the existing `grepwise/db/` package (`connection.py`, `schema.sql`) instead of a new `store/`, so fewer imports changed. Writes stay in `ingest/store.py`.
- **Settings use the `GREPWISE_` prefix** (`GREPWISE_HOME`, `GREPWISE_EMBEDDER`, …), and only this checkout's `.env` is read, never the current folder's. The MCP server runs inside other projects, whose environment must not leak in.
- **Vector cache invalidation:** `sources.generation` (a fresh `time.time_ns()` on every successful index) instead of `updated_at`, which only has one-second resolution. The cache is keyed by database file too, so two databases never share entries.
- **`sources.embedding_model`** records the model of the last successful index (it used to be a column on every chunk).
- **FTS5 `path` column holds the raw path:** the `unicode61` tokenizer already splits on `/ _ . -`.
- **Timing flag:** `grepwise search --timing`, not `--verbose`.
- **Lists as SQL parameters:** one JSON array per list, read with `json_each(?)`, so every SQL string stays a constant.

## 11. Results (2026-10-10)

| Check | Result |
|---|---|
| Tests | 253 passed in about 5 s, no Docker; ruff and mypy clean |
| `make demo-sources` | All six sources from scratch: 5,464 chunks (same as Postgres), 46.7 MB |
| Retrieval, hybrid | Hit@5 **0.90**, MRR 0.73 (target ≥ 0.85; Postgres was 0.78) |
| Retrieval, keyword | Hit@5 **0.82**, MRR 0.65 (target ≥ 0.80; Postgres was 0.35) |
| Retrieval, vector | Hit@5 0.80, MRR 0.73 |
| Vector search, warm | 0.4–0.8 ms over all six sources (target < 5 ms); cold load about 57 ms |
| Re-index | itsdangerous: 0 created, 140 reused, 1.2 s (was 16.2 s) |
| MCP over stdio | Real server and database via the MCP client: search, verify_citation, find_symbol, get_endpoint all correct |
| Race | Several processes creating the database at once: fixed and tested (the test fails without the fix) |

Not checked: a session in the Claude Code app itself (the `claude` CLI isn't installed on this machine). Restart Claude Code to pick up the new server.
