# Grepwise

Give your coding assistant the real docs and source of the libraries you use, at the exact version, with line-level citations it can check. Grepwise is an MCP server that runs on your machine: no Docker, no API keys.

**Status:** v2 in progress ([plan](docs/spec/10-v2-plan.md)). N1 done: SQLite storage, no LLM or server needed. The earlier web UI and built-in agent are archived at the `v0.1-webapp` tag.

## Setup

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`) and git.

```bash
make setup    # installs dependencies, tree-sitter grammars and the local embedding model (once)
```

Everything Grepwise stores lives in `~/.grepwise/`: the database (`grepwise.db`) and the embedding model. Delete the folder to start over.

## Index something

```bash
cd backend
uv run grepwise index https://github.com/pallets/itsdangerous --ref 2.2.0             # code
uv run grepwise index-docs https://github.com/fastapi/typer --ref 0.27.2 --path docs  # docs folder
uv run grepwise index-openapi https://petstore3.swagger.io/api/v3/openapi.json \
    --name petstore --version 1.0.27                                                  # API spec
uv run grepwise sources
```

Re-running an index command for the same version re-indexes it in place, reusing every embedding already made.

## Use it in Claude Code / Cursor

Grepwise's tools (`list_sources`, `search`, `read_file`, `find_symbol`, `get_endpoint`, `verify_citation`) are called by your editor's own model. Grepwise makes no LLM calls.

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

Then ask, for example: *"Using grepwise, how do I make an option required in Typer? Cite your sources."* A good answer cites exact lines, like `fastapi/typer@0.27.2:docs/tutorial/options/required.md:L1-L74`, which you can check with `grepwise verify "<citation>"`.

## The same tools from the terminal

```bash
uv run grepwise search "how do I make an option required?"
uv run grepwise search "find pet by id" --source petstore --kind endpoint
uv run grepwise symbol TimestampSigner
uv run grepwise read pallets/itsdangerous@2.2.0 src/itsdangerous/timed.py --lines 20-60
uv run grepwise endpoint "GET /pet/42" --source petstore
uv run grepwise verify "petstore@1.0.27:GET /pet/{petId}:L1-L27"
uv run grepwise tool search '{"query": "required option", "limit": 3}'  # raw JSON, as a model sees it
```

Search results show `vec #3 / kw #1`: where each result ranked in meaning-based (vector) and exact-word (keyword) search before the two lists were merged. Add `--timing` to see how long each step took, or `--mode keyword|vector` to try one side alone.

## How well does search work?

`evals/questions.yaml` has 56 questions over itsdangerous, Typer, Click and Petstore, each with the files a correct answer must cite.

```bash
make demo-sources     # index the eval sources (about 10 minutes the first time)
make eval-retrieval   # Hit@5 and MRR for hybrid, keyword-only and vector-only search
```

Hit@5 is the share of questions with an expected file in the top 5 results.

## Development

```bash
make test   # offline: each test gets its own temporary database
make lint   # ruff + mypy
```
