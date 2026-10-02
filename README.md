# Grepwise

Ask questions about any codebase, library docs or API, and get answers with exact, verifiable citations. Use it in the browser, or plug it into Claude Code / Cursor as an MCP server.

**Status:** M1 (repo ingestion) built. See [docs/spec/00-overview.md](docs/spec/00-overview.md) for the plan.

## Local setup

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`), Docker and git.

```bash
cp .env.example .env      # then add GEMINI_API_KEY; if port 5432 is taken, set POSTGRES_PORT and DATABASE_URL
make setup                # creates backend/.venv, installs deps, downloads tree-sitter grammars
make db                   # starts Postgres + pgvector in Docker
make migrate              # creates the tables
```

Point your editor's Python interpreter at `backend/.venv/bin/python`.

## Usage

```bash
cd backend
uv run grepwise index https://github.com/pallets/itsdangerous --ref 2.2.0
uv run grepwise stats
```

## Development

```bash
make test   # unit + integration tests; offline, never calls Gemini
make lint   # ruff + mypy
```
