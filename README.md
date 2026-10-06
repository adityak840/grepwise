# Grepwise

Ask questions about any codebase, library docs or API, and get answers with exact, verifiable citations. Use it in the browser, or plug it into Claude Code / Cursor as an MCP server.

**Status:** M6 phase 6.1 (web UI) built. See [docs/spec/00-overview.md](docs/spec/00-overview.md) for the plan.

## Local setup

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`), Docker and git.

```bash
cp .env.example .env      # GEMINI_API_KEY is only needed for the agent; if port 5432 is taken, set POSTGRES_PORT and DATABASE_URL
make setup                # creates backend/.venv, installs deps, downloads tree-sitter grammars and the embedding model
make db                   # starts Postgres + pgvector in Docker
make migrate              # creates the tables
```

Point your editor's Python interpreter at `backend/.venv/bin/python`.

## Usage

```bash
cd backend
uv run grepwise index https://github.com/pallets/itsdangerous --ref 2.2.0           # code
uv run grepwise index-docs https://github.com/fastapi/typer --ref 0.27.2 --path docs  # docs folder
uv run grepwise index-openapi https://petstore3.swagger.io/api/v3/openapi.json \
    --name petstore --version 1.0.27                                                # API spec
uv run grepwise search "how do I make an option required?"
uv run grepwise search "find pet by id" --source petstore --kind endpoint
uv run grepwise stats --type docs
```

Search results show `vec #3 / kw #1`: where each result ranked in meaning-based (vector) and
exact-word (keyword) search before the two lists were merged.

### Tools

The same five read-only tools power the web agent and the MCP server:

```bash
uv run grepwise sources                                   # list_sources
uv run grepwise symbol TimestampSigner                    # find_symbol
uv run grepwise read pallets/itsdangerous@2.2.0 src/itsdangerous/timed.py --lines 20-60
uv run grepwise endpoint "GET /pet/42" --source petstore  # get_endpoint
uv run grepwise verify "petstore@1.0.27:GET /pet/{petId}:L1-L27"
uv run grepwise tool search '{"query": "required option", "limit": 3}'  # raw JSON, as an agent sees it
```

### Ask

The agent answers in plain language, calling the tools itself, and checks every citation:

```bash
uv run grepwise ask "How do I make an option required in Typer?"
uv run grepwise ask "How does TimestampSigner check expiry?" --provider ollama   # offline
uv run grepwise runs                       # recent runs: steps, grounded, tokens, time
uv run grepwise serve                      # POST /chat (SSE) and GET /health on :8000
```

For `--provider ollama`, run `make ollama` once: it builds `grepwise-qwen3.5`, qwen3.5:9b with
a 16k context (Ollama's 4k default silently cuts off agent prompts).

### Web UI

```bash
make serve                         # the API on :8000
cd web && npm install && npm run dev   # the UI on http://localhost:3000
```

Ask questions with streaming answers, a trail of the tools the agent used, and numbered
citation chips (green when verified). Click a chip to see the exact lines, load more context, or
open them on GitHub. Visitors can optionally use their own Gemini key (kept in their browser tab).

## Use with Claude Code / Cursor

Grepwise is also an MCP server: your editor's own model calls Grepwise's tools (`list_sources`,
`search`, `read_file`, `find_symbol`, `get_endpoint`, `verify_citation`). No LLM key needed.

### Local (stdio)

Start the database and index something first (see above). Then:

**Claude Code**

```bash
claude mcp add grepwise -- uv run --directory /path/to/grepwise/backend grepwise mcp
```

**Cursor** (`~/.cursor/mcp.json`) or **Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "grepwise": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/grepwise/backend", "grepwise", "mcp"]
    }
  }
}
```

### Remote (Streamable HTTP)

`grepwise serve` also serves MCP at `/mcp`. Anonymous use is rate limited per IP; a key from
`MCP_API_KEYS` gets a higher limit:

```bash
claude mcp add --transport http grepwise https://<your-api-host>/mcp
claude mcp add --transport http grepwise https://<your-api-host>/mcp \
    --header "Authorization: Bearer <key>"
```

In production, add the API's hostname to `MCP_ALLOWED_HOSTS`.

### Try it

- *"Using grepwise, how do I make an option required in Typer? Cite your sources."*
- *"Using grepwise, what does GET /pet/{petId} return?"*
- *"Using grepwise, how does itsdangerous's TimestampSigner check expiry?"*

A good answer cites exact lines, like
`fastapi/typer@0.27.2:docs/tutorial/options/required.md:L1-L74`, which you can check with
`grepwise verify "<citation>"` (or the `verify_citation` tool). The `answer_with_citations`
prompt gives the model these instructions.

## Development

```bash
make test   # unit + integration tests; offline, never downloads models or calls Gemini
make lint   # ruff + mypy
```
