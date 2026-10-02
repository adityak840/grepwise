# Grepwise

Ask questions about any codebase, library docs or API, and get answers with exact, verifiable citations. Use it in the browser, or plug it into Claude Code / Cursor as an MCP server.

**Status:** M3 (tool layer and citation checking) built. See [docs/spec/00-overview.md](docs/spec/00-overview.md) for the plan.

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

## Development

```bash
make test   # unit + integration tests; offline, never downloads models or calls Gemini
make lint   # ruff + mypy
```
