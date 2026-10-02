# M1: Project setup, database schema, repo ingestion

**Goal:** run one command that turns a real GitHub repo into chunks with correct line ranges and embeddings, stored in Postgres.

```bash
uv run grepwise index https://github.com/pallets/itsdangerous --ref 2.2.0
```

Read [00-overview.md](00-overview.md) first. This milestone covers section 3.1 (code only) and 3.2.

---

## 1. Scope

**In scope**
- Backend project setup (uv, Python 3.12, lint, types, tests)
- Local Postgres + pgvector in Docker
- Database schema and a simple migration runner
- Repo ingestion: fetch → filter → split (code) → enrich → embed → store
- A command-line tool (CLI) with `db migrate`, `index` and `stats` commands

**Non-goals (later milestones)**
- Search of any kind (M2)
- Docs and OpenAPI splitters (M2)
- API server, agent, MCP, UI (M3+)
- GitHub Actions trigger and the `jobs` worker loop (M6). The `jobs` table is created now but not used yet.

---

## 2. Project layout

```
grepwise/
├── docker-compose.yml        # local Postgres + pgvector
├── .env.example              # every setting, no real values
├── Makefile                  # make db, make test, make lint
├── backend/
│   ├── pyproject.toml
│   ├── migrations/
│   │   └── 001_init.sql
│   ├── src/grepwise/
│   │   ├── config.py         # settings from .env
│   │   ├── cli.py            # `grepwise` command
│   │   ├── db/
│   │   │   ├── connection.py
│   │   │   └── migrate.py
│   │   └── ingest/
│   │       ├── fetch.py      # clone at a ref
│   │       ├── filters.py    # skip junk files
│   │       ├── chunkers/
│   │       │   ├── code.py   # tree-sitter splitter
│   │       │   └── fallback.py  # line windows for unsupported languages
│   │       ├── enrich.py     # context header + hash
│   │       ├── embed.py      # Embedder interface: Local, Gemini, Fake
│   │       ├── store.py      # write sources/documents/chunks
│   │       └── pipeline.py   # runs the steps in order
│   └── tests/
│       ├── fixtures/         # small sample files and a mini repo
│       └── ...
└── docs/spec/
```

---

## 3. Tech choices

| Need | Choice | Notes |
|---|---|---|
| Package manager | **uv** | Install first: `brew install uv` |
| Python | 3.12 | |
| CLI | Typer | |
| Settings | pydantic-settings | Reads `.env` |
| Postgres driver | psycopg 3 + `pgvector` (Python package) | |
| Code parsing | `tree-sitter` + `tree-sitter-language-pack` | |
| Embeddings | `google-genai` SDK | |
| Tests / lint / types | pytest, ruff, mypy | |

**Virtual environment:** uv creates and manages `backend/.venv/` (gitignored). `backend/.python-version` pins 3.12. Always run commands through `uv run …`; never install packages globally.

**Versions:** use the latest stable release of each at build time. `uv.lock` records the exact versions. Check the current docs for `google-genai` and `tree-sitter-language-pack` before writing code against them, since both change often.

---

## 4. Local database

`docker-compose.yml` runs the `pgvector/pgvector:pg17` image on port 5432, with a named volume so data survives restarts.
`make db` starts it. `uv run grepwise db migrate` creates the tables.

Neon is only used when we deploy (M6). Locally, everything runs against Docker.

---

## 5. Schema (`migrations/001_init.sql`)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE schema_migrations (
  version    text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sources (
  id          bigserial PRIMARY KEY,
  type        text NOT NULL CHECK (type IN ('repo','docs','openapi')),
  name        text NOT NULL,              -- e.g. 'pallets/itsdangerous'
  url         text NOT NULL,
  version     text NOT NULL,              -- tag or commit we indexed
  commit_sha  text,
  status      text NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','indexing','ready','failed')),
  error       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (type, name, version)
);

CREATE TABLE documents (
  id         bigserial PRIMARY KEY,
  source_id  bigint NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  path       text NOT NULL,               -- relative to repo root
  language   text,
  line_count int NOT NULL,
  UNIQUE (source_id, path)
);

CREATE TABLE chunks (
  id           bigserial PRIMARY KEY,
  document_id  bigint NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  source_id    bigint NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  kind         text NOT NULL,             -- function | method | class | module | window
  symbol       text,                      -- e.g. 'Signer.sign'
  start_line   int NOT NULL,              -- 1-based, inclusive
  end_line     int NOT NULL,              -- 1-based, inclusive
  header       text NOT NULL,             -- context header (see 6.4)
  content      text NOT NULL,
  content_hash text NOT NULL,             -- sha256 of header + content
  embedding    halfvec(768),
  tsv          tsvector GENERATED ALWAYS AS
               (to_tsvector('simple', coalesce(symbol,'') || ' ' || content)) STORED
);

CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding halfvec_cosine_ops);
CREATE INDEX chunks_tsv_gin       ON chunks USING gin (tsv);
CREATE INDEX chunks_symbol_btree  ON chunks (symbol);
CREATE INDEX chunks_source        ON chunks (source_id);
CREATE INDEX chunks_hash          ON chunks (source_id, content_hash);

CREATE TABLE jobs (
  id          bigserial PRIMARY KEY,
  source_id   bigint NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  status      text NOT NULL DEFAULT 'queued'
              CHECK (status IN ('queued','running','done','failed')),
  progress    int NOT NULL DEFAULT 0,     -- 0..100
  error       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
```

**Why `'simple'` for full-text:** the English stemmer mangles code identifiers. M2 will revisit this for docs.

**Later migrations:** `002_embedding_model.sql` adds `chunks.embedding_model` (see 6.5).

**Migration runner:** applies every `migrations/*.sql` file not yet listed in `schema_migrations`, in filename order, each inside a transaction.

---

## 6. Ingestion rules

### 6.1 Fetch
- Shallow clone (`--depth 1`) of the given `--ref` (tag or branch) into a temporary folder; record the commit SHA.
- Public GitHub URLs only. Reject anything else with a clear error.
- **Size limits:** abort if the repo has more than 5,000 files after filtering, or more than 50 MB of text.
- The temporary folder is always deleted afterwards, even on failure.

### 6.2 Filter
Skip:
- **Folders:** `.git`, `node_modules`, `vendor`, `dist`, `build`, `.next`, `__pycache__`, `.venv`, `venv`, `target`, `coverage`
- **Files:** lockfiles (`*.lock`, `package-lock.json`, `pnpm-lock.yaml`, `poetry.lock`, `uv.lock`), minified files (`*.min.js`, `*.min.css`), source maps (`*.map`)
- **Binary files:** any file with a null byte in its first 8 KB
- **Large files:** larger than 200 KB
- **Files with very long lines:** any line over 2,000 characters (almost always data, minified or generated code). This guarantees every chunk fits the size limit.

Keep everything else that is text.

### 6.3 Split
**Supported languages (tree-sitter) in M1:** Python, JavaScript, TypeScript, TSX.

| Code element | Chunk |
|---|---|
| Top-level function | One `function` chunk |
| Class that fits the size limit | One `class` chunk |
| Class over the size limit | One `method` chunk per method, plus a `class` chunk with the class signature, docstring and class-level lines |
| Top-level code between definitions (imports, constants) | Contiguous runs grouped into `module` chunks |

- **Size limit:** about 1,500 tokens per chunk (estimate tokens as characters ÷ 4).
- **Oversized function or method:** split into `window` chunks of up to the size limit, each overlapping the previous one by about 15% of its lines.
- **Unsupported languages or parse failures:** the fallback splitter makes `window` chunks of up to 80 lines (and never over the size limit), with 15 lines of overlap for full-size windows.
- **Line numbers** are 1-based and inclusive, and must match the original file exactly. This is the most important property of M1.
- **Symbols** are qualified: `Signer.sign`, not `sign`.

### 6.4 Enrich
Each chunk gets a header, which is embedded together with the content but stored separately:

```
File: src/itsdangerous/signer.py
Symbol: Signer.sign (method)
Inside: class Signer
```

`content_hash` = sha256 of header + content.

### 6.5 Embed
- An `Embedder` interface with three implementations:
  - `LocalEmbedder` (**default**, `EMBEDDER=local`): `nomic-ai/nomic-embed-text-v1.5-Q` via fastembed, run on the CPU, no quota. Adds the model's task prefixes (`search_document: ` / `search_query: `). Texts are sorted by length and grouped up to 16 at a time **and** about 1,000 estimated tokens per group, because memory grows with the square of input length and a group is padded to its longest text (measured: 1.9 GB peak, against 8+ GB with larger groups). The model (about 130 MB) downloads once, in `make setup`, to `~/.cache/grepwise/fastembed`.
  - `GeminiEmbedder` (`EMBEDDER=gemini`): model from settings (default `gemini-embedding-001`), `output_dimensionality=768`, task type `RETRIEVAL_DOCUMENT`. Note: the free tier counts **each text** as a request (about 100 a minute and 1,000 a day).
  - `FakeEmbedder`: deterministic vectors derived from the hash. Used in all tests, so **tests never call the network**.
- **Normalize every vector to length 1** before storing.
- Batch requests. On rate-limit errors, retry with exponential backoff (up to 5 tries).
- **Reuse:** every embedding is saved in `embedding_cache`, keyed by `(model_id, content_hash)` (migration `004_embedding_cache.sql`). Before embedding, the pipeline looks up the cache, so a chunk seen before, in **any** source or version, is never embedded twice by the same model. Each chunk also stores `embedding_model` (e.g. `local:nomic-ai/nomic-embed-text-v1.5-Q:768`, `gemini:gemini-embedding-001:768` or `fake:768`), and search only compares vectors from the same model.
- **Progress is saved as it goes:** new embeddings are written to the cache after every 64, so a run that fails halfway resumes from where it stopped.

### 6.6 Store
- Upsert the `sources` row; set status `indexing`, then `ready` or `failed` (with the error message).
- Re-indexing the same source and version replaces its documents and chunks in one transaction.

---

## 7. Configuration (`.env.example`)

```
DATABASE_URL=postgresql://grepwise:grepwise@localhost:5432/grepwise
GEMINI_API_KEY=          # for the agent (M4) and EMBEDDER=gemini
EMBEDDING_MODEL=gemini-embedding-001
EMBEDDING_DIM=768
EMBEDDER=local         # local | gemini | fake
LOCAL_EMBEDDING_MODEL=nomic-ai/nomic-embed-text-v1.5-Q
```

The real `.env` is gitignored. The key is never logged or printed.

---

## 8. CLI

| Command | Does |
|---|---|
| `grepwise db migrate` | Applies pending migrations |
| `grepwise index <url> --ref <tag>` | Runs the full pipeline and prints a summary (files kept/skipped, chunks by kind, embeddings created/reused, time taken) |
| `grepwise stats` | Lists sources with status, chunk counts and database size |

---

## 9. Acceptance criteria

M1 is done when all of these pass:

**Automated (`make test`)**
- [ ] **Chunker tests:** for fixture files in Python and TypeScript, the exact expected chunks (kind, symbol, start line, end line) are produced. Includes a large class split into methods, an oversized function split into windows, and top-level code grouped into module chunks.
- [ ] **Line accuracy test:** for every chunk of every fixture, the file's lines `start_line..end_line` joined together equal `content` exactly.
- [ ] **Filter tests:** each skip rule has a test.
- [ ] **Normalization test:** stored vectors have length 1 (± 0.001).
- [ ] **Integration test** (against Docker Postgres, with `FakeEmbedder`): migrate, index a small fixture repo, check row counts, re-index, and confirm embeddings are reused, not recreated.
- [ ] `ruff check` and `mypy` pass with no errors.

**Manual**
- [ ] `grepwise index https://github.com/pallets/itsdangerous --ref 2.2.0` with the local embedder finishes, and `stats` shows the source as `ready`.
- [ ] Opening 3 random chunks and comparing them with the file on GitHub at that tag shows identical lines.
- [ ] Running the same command again reports all embeddings reused.

---

## 10. Review checklist (after the build)
- Line numbers exact? (the most important check)
- Temporary folders always cleaned up?
- No secrets in logs, code or git history?
- Tests run offline?
- Code readable and matching this spec?
