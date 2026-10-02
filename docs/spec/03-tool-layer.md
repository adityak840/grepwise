# M3: Tool layer and citation checking

**Goal:** five well-defined tools that an agent (M4) or an MCP client (M5) can call, each with a typed input, a compact JSON output and exact citations, plus a function that checks any citation against the database.

```bash
uv run grepwise sources
uv run grepwise symbol TimestampSigner
uv run grepwise read pallets/itsdangerous@2.2.0 src/itsdangerous/timed.py --lines 20-60
uv run grepwise endpoint "GET /pet/42" --source petstore
uv run grepwise verify "petstore@1.0.27:GET /pet/{petId}:L1-L27"
```

Read [00-overview.md](00-overview.md) section 3.4 first. M3 builds on M2's search, citations and stored document text.

---

## 1. Scope

**In scope**
- `grepwise/tools/`: the five tools as plain functions, with Pydantic input and output models
- A tool registry that exposes each tool's name, description and JSON Schema, plus one `call_tool()` entry point
- Structured tool errors that tell the caller how to recover
- Citation checking (`verify_citation`)
- Size caps on every tool output
- CLI commands for each tool, plus `verify`
- **CLI search change:** `grepwise search` no longer applies the token budget by default (it only prints citations); a new `--budget N` flag applies it. The `search` **tool** keeps the budget.

**Non-goals (later)**
- The agent loop and LLM calls (M4)
- MCP server and HTTP API (M5)
- Any write or indexing tool. All M3 tools are **read-only**.

---

## 2. Package layout

```
src/grepwise/tools/
├── __init__.py      # registry: TOOLS, tool_schemas(), call_tool()
├── context.py       # ToolContext (conn, embedder, settings)
├── errors.py        # ToolError
├── sources.py       # resolving 'name' / 'name@version'; list_sources
├── search.py        # search tool (wraps search/hybrid.py)
├── read_file.py
├── find_symbol.py
├── get_endpoint.py
└── verify.py        # verify_citation
```

Tools never import FastAPI, MCP or LLM code. They take a `ToolContext` and a validated input model, and return an output model.

---

## 3. Shared rules

### 3.1 Source references
A **source reference** is `name` or `name@version` (e.g. `fastapi/typer` or `fastapi/typer@0.27.2`).
- For **search** and **find_symbol**, `name` alone means all versions.
- For **read_file** and **get_endpoint**, `name` alone is fine if only one version is indexed; if several are, return a `ToolError` listing them.
- A repo and its docs can share a name (`fastapi/typer` as `repo` and as `docs`). That's fine: a path identifies the document, and the same path at the same version has the same content in both.

### 3.2 Output size caps
Every tool output is JSON with a hard cap (default **12,000 characters**, `TOOL_MAX_CHARS`). Tools cap their own content first (fewer results, fewer lines) and set `"truncated": true` when they do. Caps exist because tool output goes straight into an LLM's context.

### 3.3 Errors
Tools never raise to the caller for expected problems. They return a `ToolError`:
```json
{"error": {"code": "source_not_found", "message": "No source named 'typr'.",
           "hint": "Available: fastapi/typer, pallets/itsdangerous, petstore"}}
```
Codes: `bad_input`, `source_not_found`, `ambiguous_source`, `file_not_found`, `bad_line_range`, `endpoint_not_found`, `not_reindexed` (a document from before M2 has no stored text), `internal`.
The hint should tell an agent exactly what to do next.

### 3.4 Content is data
File and docs text in tool outputs may contain instructions ("ignore previous instructions…"). Tools return it unchanged, inside a clearly named field (`content`), and never interpret it. M4 handles prompt injection; M3 just never mixes content into messages or descriptions.

---

## 4. The tools

### 4.1 `list_sources`
**Input:** `type?` (repo | docs | openapi)
**Output:** for each source: `ref` (`name@version`), `type`, `status`, `documents`, `chunks`, `subpath`, `indexed_at`, and `embedding_model`.
**Purpose:** lets an agent discover what it can search.

### 4.2 `search`
**Input:** `query`, `sources?` (list of refs), `kinds?`, `limit?` (default 8, max 20)
**Output:** results with `citation`, `source`, `source_type`, `kind`, `symbol`, `content`, `found_by` (`{"vector": 2, "keyword": 1}`), and `truncated`.
Wraps M2's hybrid search with the token budget (`SEARCH_TOKEN_BUDGET`).

### 4.3 `read_file`
**Input:** `source` (ref), `path`, `start_line?`, `end_line?`
**Output:** `citation` for the exact range returned, `start_line`, `end_line`, `total_lines`, `content`, `truncated`.
- Reads from `documents.content`.
- No range: return from line 1.
- **Cap:** at most **200 lines** or the character cap, whichever is smaller. If the request is larger, return the first part, set `truncated`, and include `next_start_line`.
- A range past the end of the file returns `bad_line_range` with the file's `total_lines` in the hint.
- Works for OpenAPI virtual documents too (`path = "GET /pet/{petId}"`).
- Documents with no stored text return `not_reindexed`.

### 4.4 `find_symbol`
**Input:** `name`, `sources?`, `kinds?`
**Output:** up to 20 matches, each with `citation`, `symbol`, `kind`, `source`, `path`, and `signature` (the first non-comment, non-decorator line of the chunk).
**Matching:**
- Exact match on the qualified symbol (`Signer.sign`), **or** on its last part (`sign` matches `Signer.sign` and `sign`).
- Case-sensitive first. If nothing matches, case-insensitive.
- Docs sections are symbols too (`Required CLI Options` matches the last part of `... > Required CLI Options`).
- Must use an index, not a scan. Migration `005_symbol_lookup.sql` adds a generated `symbol_short` column (the part after the last `.` or ` > `) with a B-tree index, plus indexes on `lower(symbol)` and `lower(symbol_short)`.

### 4.5 `get_endpoint`
**Input:** `method?`, `path`, `source?`
**Output:** `citation`, `source`, `method`, `path` (the template), and `content` (the full rendering, which is already capped at ingestion).
**Matching**, in order:
1. Exact template: `GET /pet/{petId}`.
2. A concrete path matched against templates: `GET /pet/42` matches `GET /pet/{petId}`. Each `{param}` matches one path segment. If several templates match, prefer the one with the most literal (non-parameter) segments.
3. No method given: return all methods for the matching path (up to 10).
Only `openapi` sources are searched.

---

## 5. Citation checking (`tools/verify.py`)

```python
def verify_citation(conn, citation: str) -> VerifiedCitation | ToolError
```
1. Parse with M2's `parse_citation`.
2. Find documents with that source name, version and path (any source type).
3. Check `1 <= start <= end <= line_count`.
4. Return `VerifiedCitation(citation, source, path, start_line, end_line, content)`, where `content` is exactly those lines.

This is how M4 removes made-up citations: anything that doesn't verify is dropped. Also exposed as `grepwise verify <citation>`.

---

## 6. Registry (`tools/__init__.py`)

```python
@dataclass(frozen=True)
class Tool:
    name: str
    description: str          # written for an LLM: what it does and when to use it
    input_model: type[BaseModel]
    run: Callable[[ToolContext, BaseModel], BaseModel]

TOOLS: dict[str, Tool]
def tool_schemas() -> list[dict]           # [{name, description, input_schema}]
def call_tool(ctx, name: str, args: dict) -> dict   # validates args, runs, applies caps; never raises
```
- `input_schema` is the Pydantic model's JSON Schema. Every field has a description.
- `call_tool` turns validation errors into `bad_input` errors (with the field and reason) and unexpected exceptions into `internal` errors, logging the traceback without leaking it into the output.
- Tool descriptions are part of the product: clear, short, and saying when to prefer each tool (e.g. "Use find_symbol when you know an exact name; use search for questions").

---

## 7. CLI

| Command | Calls |
|---|---|
| `grepwise sources [--type t]` | `list_sources` |
| `grepwise symbol <name> [--source s] [--kind k]` | `find_symbol` |
| `grepwise read <source> <path> [--lines 20-60]` | `read_file` |
| `grepwise endpoint "<METHOD path>" [--source s]` | `get_endpoint` |
| `grepwise verify "<citation>"` | `verify_citation` |
| `grepwise tool <name> '<json args>'` | `call_tool`, printing raw JSON (exactly what an agent would see) |
| `grepwise search ...` | unchanged, but **no budget unless `--budget N`** is given |

`stats` stays as it is.

---

## 8. Configuration
Optional: `TOOL_MAX_CHARS=12000`, `READ_MAX_LINES=200`.

---

## 9. Acceptance criteria

**Automated (`make test`, offline, FakeEmbedder)**
- [ ] **Registry:** every tool has a description and a valid JSON Schema with described fields; `call_tool` with bad arguments returns `bad_input` naming the field; an exception inside a tool returns `internal` without a traceback in the output.
- [ ] **list_sources:** lists every fixture source with correct counts; the type filter works.
- [ ] **search tool:** respects `limit` and the token budget; `found_by` matches the ranks.
- [ ] **read_file:** exact lines for code, docs and OpenAPI documents; no range starts at line 1; the 200-line cap sets `truncated` and `next_start_line`; a range past the end returns `bad_line_range`; a name with several versions returns `ambiguous_source`; a pre-M2 document returns `not_reindexed`.
- [ ] **find_symbol:** qualified match, last-part match, case-insensitive fallback, docs-section match, and source and kind filters. A test checks that the query plan uses an index (`EXPLAIN`), not a sequential scan.
- [ ] **get_endpoint:** exact template, concrete path (`/pet/42`), most-literal preference (`/pet/findByStatus` beats `/pet/{petId}`), no method (all methods), and `endpoint_not_found` with a hint.
- [ ] **verify_citation:** valid citations return exact content; a wrong path, an unknown source, an end past the file, or a malformed citation all return errors.
- [ ] **Caps:** no tool output exceeds `TOOL_MAX_CHARS` when serialized, even with large fixtures.
- [ ] **CLI search:** with no `--budget`, returns `--limit` results even when they're long; with `--budget`, trims.
- [ ] `make lint` passes.

**Manual (local embedder, sources indexed in M2)**
- [ ] `grepwise symbol TimestampSigner` → `TimestampSigner` in `timed.py`, with its signature line.
- [ ] `grepwise endpoint "GET /pet/42" --source petstore` → `GET /pet/{petId}`.
- [ ] `grepwise read` on that endpoint's citation range returns the same text that `verify` returns.
- [ ] `grepwise tool search '{"query": "required option", "limit": 3}'` prints compact JSON under the cap.

---

## 10. Review checklist
- Every tool read-only?
- All user input passed as SQL parameters?
- Errors structured, with useful hints, and no tracebacks leaked?
- Output caps enforced in one place (`call_tool`) as well as in each tool?
- `find_symbol` uses indexes?
- Content returned as data, never mixed into descriptions?
