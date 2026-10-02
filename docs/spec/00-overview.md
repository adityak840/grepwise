# Grepwise: Project Overview

> Ask questions about any codebase, library docs or API, and get answers with exact, verifiable citations.
> Use it in the browser, or plug it into Claude Code / Cursor as an MCP server.

This is the source of truth for what we're building. Each milestone gets its own spec file in this folder (`01-*.md`, `02-*.md`, …) before we build it.

---

## 1. What Grepwise does

1. **Indexes sources**: GitHub repos, library documentation and OpenAPI specs.
2. **Answers questions** about them, citing the exact file and lines (e.g. `nextjs@16:docs/routing.mdx:L40-L58`).
3. **Combines code and docs.** For example: *"My repo uses Next.js 14 patterns. What breaks in Next.js 16?"*

Two ways to use it:

| Interface | Who does the reasoning | For |
|---|---|---|
| **Web app** | Grepwise's own agent (Gemini) | Anyone with a browser |
| **MCP server** | The user's own AI (Claude Code, Cursor) | Developers inside their editor |

---

## 2. Architecture at a glance

```
  GitHub repo / Docs / OpenAPI spec
              │
              ▼
   ┌──────────────────────┐
   │ 1. INGESTION         │  fetch → filter → split → embed
   │  (GitHub Actions)    │
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │ 2. POSTGRES (Neon)   │  chunks + vectors + full-text index
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │ 3. RETRIEVAL         │  hybrid search (vector + keyword)
   └──────────┬───────────┘
              ▼
   ┌──────────────────────┐
   │ 4. TOOL LAYER        │  search · read_file · find_symbol
   │   (shared core)      │  · get_endpoint · list_sources
   └─────┬──────────┬─────┘
         ▼          ▼
  ┌────────────┐ ┌────────────┐
  │ 5. WEB     │ │ 6. MCP     │
  │   AGENT    │ │   SERVER   │
  │ (Gemini)   │ │ (no LLM)   │
  └─────┬──────┘ └─────┬──────┘
        ▼              ▼
  7. Next.js UI   Claude Code / Cursor
```

**Core idea:** the tool layer is shared. The web agent and the MCP server are two front doors to the same tools.

---

## 3. Components

### 3.1 Ingestion: sources → searchable chunks

| Step | What happens |
|---|---|
| **Fetch** | Repo: shallow `git clone` at a tag or commit. Docs: the Markdown/MDX docs folder in the library's repo (or its `llms.txt`). API: download the OpenAPI JSON. |
| **Filter** | Skip `node_modules`, lockfiles, binaries, generated files and very large files. |
| **Split** | One splitter per source type (below). |
| **Enrich** | Attach path, line range, symbol name, kind and a context header ("file X, inside class Y"). |
| **Embed** | Gemini embeddings, in batches. Unchanged chunks are skipped using a content hash. |
| **Store** | Write to Postgres and mark the source `ready`. |

**Splitters:**
- **Code:** tree-sitter splits by function or class. Very long functions are split further, with overlap.
- **Docs:** by heading section. Each chunk keeps its heading path (*Routing > Dynamic Routes*).
- **OpenAPI:** one chunk per endpoint (method, path, parameters, request, responses).

**Where it runs:** user-submitted repos are indexed by a **GitHub Actions workflow** triggered by the API. Preset docs sets are indexed from a laptop with the same command-line tool.

### 3.2 Database: Postgres (Neon) with pgvector

| Table | Holds |
|---|---|
| `sources` | type (`repo` / `docs` / `openapi`), URL, **version**, status |
| `documents` | one row per file or page |
| `chunks` | text, kind, symbol, start and end lines, `embedding` (halfvec, 768 dims), `tsv` (full-text) |
| `jobs` | indexing queue: status, progress, error |

**Indexes:** HNSW on `embedding`, GIN on `tsv`, b-tree on `symbol`.
**Storage budget:** Neon's free tier is 0.5 GB, so we use 768-dimension `halfvec` vectors to roughly halve vector storage.
**Versions:** the same library can be indexed at two versions (e.g. Next.js 14 and 16) to answer "what changed?" questions.

### 3.3 Retrieval: hybrid search
1. **Vector search:** top 50 by meaning (cosine similarity).
2. **Keyword search:** top 50 by Postgres full-text ranking (catches exact names like `handleRefreshToken`).
3. **Merge** with reciprocal rank fusion (RRF).
4. **Trim** to a token budget and return the top results.

Filters: source, version, chunk kind.

### 3.4 Tool layer: the shared core
Plain Python functions, with no web or MCP code inside.

| Tool | Does |
|---|---|
| `list_sources()` | What's indexed, and at which versions |
| `search(query, sources?, kind?)` | Hybrid search; short ranked snippets |
| `read_file(source, path, lines)` | Fuller context around a result (size-capped) |
| `find_symbol(name)` | Exact lookup of a function or class |
| `get_endpoint(method, path)` | Full details of one API endpoint |

**Citation format (everywhere):** `source@version:path:L<start>-L<end>`

### 3.5 Web agent
FastAPI endpoint `POST /chat`:
1. Gemini receives the question and the tool definitions.
2. It calls tools in a loop (up to about 8 steps).
3. It returns a structured answer: `{ answer, citations[] }`.
4. **Every citation is checked against the database**; invalid ones are removed.
5. Everything streams to the UI over SSE: `tool_call`, `tool_result`, `token`, `citations`, `done`.

The LLM provider is behind one interface (OpenAI-compatible), so switching from Gemini to Groq, OpenRouter, OpenAI or Claude is a config change.

### 3.6 MCP server
- Exposes the same 5 tools, plus each indexed source as a resource.
- **Makes no LLM calls.** The client's model does the reasoning.
- **Two ways to connect:**
  - **Local (stdio):** `uvx grepwise`, published on PyPI.
  - **Remote (Streamable HTTP):** the hosted instance, with an API key.
- Indexing new sources needs auth (it costs embedding quota).

### 3.7 Frontend (Next.js)

| Screen | What's on it |
|---|---|
| **Sources** | Add a repo URL or choose a preset docs set; live indexing progress |
| **Chat** | Source picker, streaming answer, tool-call trail, citations |
| **Viewer** | Code or docs with the cited lines highlighted |
| **Evals** | Accuracy per eval set |

---

## 4. Two flows, start to finish

**Indexing a repo**
```
User pastes URL → API creates source + job → triggers GitHub Actions
→ clone → filter → split → embed → store → status: ready → UI updates
```

**Answering a question**
```
"How does auth work with the Stripe API here?"
→ list_sources → search(repo, "auth") → read_file(...)
→ search(stripe-api, "authentication") → get_endpoint(...)
→ structured answer → citations checked → streamed to UI
```

---

## 5. Evals

| Eval set | Measures |
|---|---|
| Code (~20 questions) | Right file in top 5? Answer correct? |
| Docs (~15) | Right section cited? |
| Cross-source (~10–15) | Both repo and docs cited correctly? |
| With vs. without MCP | Claude Code's accuracy on the same questions, with and without Grepwise |

- Questions live in YAML and are **written by hand, not generated**.
- `make eval` produces a report, which the Evals screen shows.

---

## 6. Tech stack

| Layer | Choice |
|---|---|
| Backend | Python, FastAPI, Pydantic, uv (package manager) |
| Parsing | tree-sitter, Markdown parser, OpenAPI loader |
| Database | PostgreSQL + pgvector (Neon) |
| LLM | Gemini Flash (tool calling + structured outputs) |
| Embeddings | Gemini embeddings, 768 dims |
| MCP | Official Python MCP SDK |
| Frontend | Next.js, TypeScript, Tailwind, shadcn/ui |
| Testing | pytest, Vitest |
| CI / worker | GitHub Actions |

---

## 7. Hosting: free, no card

| Piece | Runs on | Note |
|---|---|---|
| Frontend | Vercel (Hobby) | `grepwise.adityakhandelwal.me` |
| API + remote MCP | Render (free web service) | Sleeps after 15 min idle; the UI shows "waking up" and warms it on page load |
| Database | Neon (free) | 0.5 GB, never pauses |
| Indexing worker | GitHub Actions | Free for public repos |
| LLM + embeddings | Gemini API (free tier) | Rate-limit visitors; optional "use your own key" |

**Guardrails:** public repos only, a repo size limit, per-visitor rate limiting, read-only tools by default.
**Secrets:** the Gemini key lives in a local `.env` file (never committed) and in Render's environment settings.

---

## 8. Milestones (about 3 weekends)

| # | Milestone | Done when |
|---|---|---|
| M1 | Project setup, DB schema, repo ingestion | A real repo is chunked with correct line ranges |
| M2 | Docs and OpenAPI splitters, hybrid search | `search()` finds exact names and conceptual matches |
| M3 | Tool layer + citations | All 5 tools work from the command line with valid citations |
| M4 | Web agent + SSE | Streamed answers with checked citations |
| M5 | MCP server (stdio + HTTP) | Works inside Claude Code |
| M6 | UI, evals, deployment, PyPI release, README | Live URL plus eval numbers |

Each milestone ends with a code review before the next one starts.

---

## 9. Non-goals (for now)
- Private repos
- User accounts and multi-tenancy
- Crawling arbitrary docs websites (preset docs sets only)
- Self-hosted LLMs or embedding models

---

## 10. Glossary

| Term | Meaning |
|---|---|
| **Agent** | An LLM that can call tools in a loop and decide its next step, instead of answering in one shot |
| **RAG** (retrieval-augmented generation) | Find relevant text first, then give it to the LLM so it answers from real sources |
| **Chunk** | A small piece of a file (a function, a docs section, an endpoint) that gets indexed and searched |
| **Embedding** | A list of numbers representing a text's meaning; similar meanings give nearby numbers |
| **pgvector** | A Postgres extension that stores embeddings and finds the nearest ones |
| **halfvec** | A pgvector type storing each number in half the space (16-bit instead of 32-bit) |
| **HNSW** | An index type that makes nearest-vector search fast |
| **Full-text search / tsvector** | Postgres's built-in keyword search; `tsvector` is the pre-processed searchable form of text |
| **GIN index** | The Postgres index type that makes full-text search fast |
| **Hybrid search** | Running vector and keyword search together and merging the results |
| **RRF** (reciprocal rank fusion) | A simple way to merge two ranked lists by rewarding items ranked high in either |
| **tree-sitter** | A fast parser that understands code structure (functions, classes) in many languages |
| **OpenAPI** | A standard JSON/YAML format describing a REST API's endpoints |
| **llms.txt** | A file some docs sites publish listing their docs in an AI-friendly form |
| **Structured outputs** | Forcing the LLM to reply in a fixed JSON shape |
| **Tool calling** | The LLM asks to run a named function with arguments; our code runs it and returns the result |
| **SSE** (Server-Sent Events) | A simple way for a server to stream updates to the browser over one HTTP connection |
| **MCP** (Model Context Protocol) | An open standard that lets AI apps (Claude Code, Cursor) connect to external tools and data |
| **stdio / Streamable HTTP** | The two ways an MCP client connects: a local process, or a remote server over HTTP |
| **uvx** | Runs a Python tool straight from PyPI without installing it first |
| **Evals** | A fixed test set of questions used to measure answer and retrieval quality |
| **Cold start** | The delay while a sleeping free server wakes up |
