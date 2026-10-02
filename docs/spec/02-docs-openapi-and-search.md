# M2: Docs and OpenAPI ingestion, hybrid search

**Goal:** index library docs and OpenAPI specs alongside repos, and search all of them with hybrid search that returns ranked results with citations.

```bash
uv run grepwise index-docs https://github.com/fastapi/typer --ref 0.27.2 --path docs
uv run grepwise index-openapi https://petstore3.swagger.io/api/v3/openapi.json --name petstore --version 1.0.27
uv run grepwise search "how do I make an option required?"
```

Read [00-overview.md](00-overview.md) (sections 3.1–3.3) and [01-setup-and-ingestion.md](01-setup-and-ingestion.md) first. M2 reuses M1's pipeline pieces (fetch, filters, enrich, embed, store) wherever possible.

---

## 1. Scope

**In scope**
- Docs splitter (Markdown and MDX) and an `index-docs` command
- OpenAPI loader and endpoint splitter, and an `index-openapi` command
- Better keyword search: identifier splitting, plus English and exact matching
- Query embeddings (the embedder's query mode)
- Hybrid search: vector + keyword, merged with RRF, trimmed to a token budget
- Citation formatting
- Storing full document text (needed by `read_file` in M3)
- A `search` CLI command that shows why each result was found

**Non-goals (later)**
- The tool layer, `read_file`, `find_symbol`, `get_endpoint` (M3)
- Docs from `llms.txt` or websites. Docs come only from a repo folder in M2.
- Reranking with a model
- Evals (M6). M2 has a small sanity check only.
- Headings inside MDX/JSX component blocks without a blank line before them aren't detected (a limitation of standard Markdown parsing).
- Indexing triggered from the web API or MCP. When that's added (M5+), it must accept only `https` URLs, block private and internal addresses, and never read local files.

---

## 2. Schema changes: `migrations/003_docs_search.sql`

```sql
-- Full text of each document, so M3's read_file can return exact lines.
ALTER TABLE documents ADD COLUMN content text;

-- Docs sources index one folder of a repo.
ALTER TABLE sources ADD COLUMN subpath text;

-- Keyword search reads search_text (content + split identifiers), filled in by Python.
ALTER TABLE chunks DROP COLUMN tsv;
ALTER TABLE chunks ADD COLUMN search_text text;
ALTER TABLE chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
  to_tsvector('english', coalesce(search_text, coalesce(symbol, '') || ' ' || content))
  || to_tsvector('simple', coalesce(search_text, coalesce(symbol, '') || ' ' || content))
) STORED;
CREATE INDEX chunks_tsv_gin ON chunks USING gin (tsv);
```

- `english` gives word roots for prose ("routing" matches "route"). `simple` keeps exact tokens for code. Storing both covers both.
- Rows indexed before this migration keep working (`search_text` is NULL, so the fallback expression is used); re-indexing fills `search_text` and `content`.
- New chunk kinds: `section` (docs) and `endpoint` (OpenAPI).

---

## 3. Docs ingestion

### 3.1 Command
```
grepwise index-docs <github-url> --ref <tag> --path <folder> [--name <name>]
```
- Fetches the repo exactly like M1, then indexes only `.md` and `.mdx` files under `--path`.
- Source: `type='docs'`, `name` = the repo name (e.g. `fastapi/typer`) unless `--name` is given, `version` = the ref, `subpath` = the folder.
- All M1 filter rules still apply (size, long lines, binary).

### 3.2 Splitting rules (`chunkers/markdown.py`)
- Parse with **markdown-it-py**. Its block tokens carry line numbers, and it knows that a `#` inside a code block is not a heading.
- **One `section` chunk per heading**, running from that heading to the line before the next heading of **any** level.
- **Symbol = heading path**, e.g. `Tutorial > CLI Options > Required Options`. Strip Markdown formatting from heading text.
- **Heading with no body** (immediately followed by a subheading): merge it into the next section, so its title still appears in that chunk.
- **Text before the first heading** (front matter, MDX imports, intro): its own `section` chunk with symbol = the file's title (front matter `title:` if present, else the file name). Skipped if it has no letters or digits.
- **Front matter** only counts if the block between the `---` fences reads as YAML settings; otherwise the `---` lines are horizontal rules.
- **Oversized sections:** split with M1's `size_windows`, keeping kind `section` and the same symbol.
- **Exact lines:** the same rule as M1. Content equals the file's lines `start..end` exactly.

### 3.3 Header
```
Docs: fastapi/typer
File: docs/tutorial/options/required.md
Section: Tutorial > CLI Options > Required Options
```
The header names the source **without its version**. It's embedded and hashed, so including the version would re-embed every chunk on each new version. Citations still carry the version.

---

## 4. OpenAPI ingestion

### 4.1 Command
```
grepwise index-openapi <url-or-file> --name <name> --version <version>
```
- Accepts a URL (http/https) or a local file, in JSON or YAML (OpenAPI 3.x; Swagger 2.0 is a non-goal).
- Download limit: 20 MB. Timeout: 60 s.
- Source: `type='openapi'`, `url` = the URL or file path given.

### 4.2 One virtual document per endpoint
OpenAPI files are often one huge file, so each **operation** (one method on one path) becomes its own **virtual document**:
- `path` = `GET /pet/{petId}` (method in capitals, then the path)
- `content` = a readable rendering of that operation (below)
- One `endpoint` chunk covering the whole rendering, lines `1..n`, symbol = the same as the path

This keeps citations exact and checkable: `petstore@1.0.27:GET /pet/{petId}:L1-L42`.

### 4.3 Rendering an operation
Plain text in this order, omitting empty sections:
```
GET /pet/{petId}
Operation: getPetById
Summary: Find pet by ID
Tags: pet

Description:
...

Parameters:
- petId (path, required, integer): ID of pet to return

Request body (application/json):
  <schema>

Responses:
- 200: successful operation
  <schema>
- 404: Pet not found
```
- **Resolve `$ref`s** inline so each endpoint stands alone, up to a depth of 3. Deeper references, and circular ones, are shown as `→ SchemaName`.
- Render schemas compactly: one line per property, `name (type, required): description`, indented for nesting.
- Cap the rendering at `MAX_CHUNK_CHARS`. If it's longer, cut it at a line boundary and add a final line `… (truncated)`.

### 4.4 Header
```
API: petstore
Endpoint: GET /pet/{petId}
```
No version in the header, for the same reason as docs.

---

## 5. Keyword search improvements (`ingest/keywords.py`)

`search_text` = the chunk content **plus** extra words from splitting identifiers in the symbol and content:
- `refreshToken` → `refresh token`
- `handle_refresh_token` → `handle refresh token`
- `HTTPServerError` → `http server error`

So the question "refresh token" matches code that only says `refreshToken`. Applied to every chunk type.

---

## 6. Hybrid search (`search/hybrid.py`)

```python
def search(
    conn, embedder, query: str, *,
    sources: list[str] | None = None,   # 'name' or 'name@version'
    kinds: list[str] | None = None,     # e.g. ['function', 'section']
    limit: int = 10,
    token_budget: int = 4_000,
) -> list[SearchResult]
```

### 6.1 Steps
1. **Embed the query** with `Embedder.embed_query()`. The local model adds the `search_query: ` prefix (chunks use `search_document: `); Gemini uses task type `RETRIEVAL_QUERY` (chunks use `RETRIEVAL_DOCUMENT`). Same model and dimension, normalized.
2. **Vector search:** the top 50 by cosine distance (`embedding <=> query`), with the filters applied, restricted to chunks whose `embedding_model` equals the embedder's `model_id`.
   - Set `hnsw.ef_search = 100`.
   - Set `hnsw.iterative_scan = relaxed_order`, so filtered queries still return enough results (pgvector 0.8+).
3. **Keyword search:** the top 50 by `ts_rank_cd(tsv, query)`, with the filters applied.
   - Build the query from the question's words with **OR between words**, using both the `english` and `simple` configurations. Natural questions contain words that won't all be in one chunk, so requiring every word would miss good results.
   - Never build SQL by joining strings. User text is always passed as a parameter.
4. **Merge with RRF:** `score = Σ 1 / (60 + rank)` across the two lists.
   Steps 2–4 and the details lookup run in **one REPEATABLE READ transaction**, so they share one snapshot and a re-index finishing mid-search can't make results disappear. The query is embedded before the transaction opens.
5. **Trim:** go down the merged list, adding results until `limit` is reached or the next one would go over `token_budget` (estimated as characters ÷ 4). Always return at least one result if there are any.

### 6.2 Which sources are searched
All sources, whatever their status. Chunks are only ever written by a successful index (M1's atomic replace), so a source marked `failed` after a failed **re-index** still has its last good chunks, and those stay searchable. If a source name matches several versions and none is given, search all of them.

### 6.3 Result
```python
@dataclass
class SearchResult:
    citation: str          # 'fastapi/typer@0.27.2:docs/tutorial/options/required.md:L1-L24'
    source: str            # 'fastapi/typer@0.27.2'
    source_type: str       # repo | docs | openapi
    path: str
    kind: str
    symbol: str | None
    start_line: int
    end_line: int
    content: str
    score: float           # RRF score
    vector_rank: int | None   # position in the vector list, if it appeared there
    keyword_rank: int | None  # position in the keyword list, if it appeared there
```

### 6.4 Citation format (`search/citations.py`)
`<name>@<version>:<path>:L<start>-L<end>`, plus a `parse_citation()` that reverses it. Paths may contain `:` and spaces (OpenAPI paths), so parse from the right.

---

## 7. CLI additions

| Command | Does |
|---|---|
| `grepwise index-docs <url> --ref <tag> --path <folder> [--name]` | Indexes a docs folder |
| `grepwise index-openapi <url-or-file> --name <n> --version <v>` | Indexes an OpenAPI spec |
| `grepwise search "<query>" [--source s] [--kind k] [--limit n]` | Prints results: rank, citation, kind, symbol, and **`vec #3 / kw #1`**, showing which searches found it |

`stats` gains a `type` filter: `grepwise stats --type docs`.

---

## 8. Configuration
No new required settings. Optional: `SEARCH_CANDIDATES=50`, `SEARCH_TOKEN_BUDGET=4000`.

---

## 9. Acceptance criteria

**Automated (`make test`, offline)**
- [ ] **Markdown splitter:** fixture `.md` and `.mdx` files give the exact expected sections (kind, symbol, lines), covering: nested headings, a `#` inside a fenced code block (not a heading), a heading with no body (merged), front matter, and an oversized section split into windows.
- [ ] **Line accuracy** for every docs chunk, the same check as M1.
- [ ] **OpenAPI:** a fixture spec (Petstore-like, with `$ref`s, one circular reference and one huge schema) gives one virtual document and chunk per operation; `$ref`s are resolved; the circular reference shows `→ Name`; the huge one ends with `… (truncated)`; YAML and JSON give identical results.
- [ ] **Identifier splitting** unit tests (camelCase, snake_case, acronyms, digits).
- [ ] **Citations:** format and parse round-trip, including OpenAPI paths with spaces and `{}`.
- [ ] **Hybrid search integration test** (FakeEmbedder, test database): index the mini repo, a docs fixture and the OpenAPI fixture, then:
  - an exact identifier query (`refreshToken`) returns that chunk first, found by keyword search;
  - `--source` and `--kind` filters return only matching results;
  - results respect `limit` and `token_budget`;
  - RRF ordering is correct for a constructed case (unit test on the merge function alone).
- [ ] The migration applies cleanly to a database that already has M1 data, and `search` works on rows indexed before the migration.
- [ ] `make lint` passes.

**Manual (local embedder)**
- [ ] Index `fastapi/typer` docs and the Petstore spec, plus `pallets/itsdangerous` from M1 (re-index it to fill `search_text`).
- [ ] `grepwise search "how do I make an option required?"` puts a Typer docs section about required options in the top 3.
- [ ] `grepwise search "sign a value with a timestamp"` puts `TimestampSigner` code from itsdangerous in the top 3.
- [ ] `grepwise search "find pet by id" --source petstore` returns `GET /pet/{petId}` first.
- [ ] For each, the `vec / kw` column makes sense (exact names found by keyword, paraphrases by vector).

---

## 10. Review checklist
- Line numbers exact for docs chunks?
- Code-block headings never treated as headings?
- `$ref` resolution can't loop forever?
- No SQL built from user text?
- Vector search filtered by `embedding_model`?
- Tests offline?
