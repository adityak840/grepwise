# Grepwise

Give your coding assistant the real docs and source of the libraries you use, at the exact version, with line-level citations it can check. Grepwise is an MCP server that runs on your machine: no Docker, no API keys.

**Status:** v2 in progress ([plan](docs/spec/10-v2-plan.md)). N1 done: SQLite storage, no LLM or server needed. N2 done: index your project's dependencies by name. N3 done: three MCP tools, libraries indexed on first use. The earlier web UI and built-in agent are archived at the `v0.1-webapp` tag.

## Setup

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`) and git.

```bash
make setup    # installs dependencies, tree-sitter grammars and the local embedding model (once)
```

Everything Grepwise stores lives in `~/.grepwise/`: the database (`grepwise.db`) and the embedding model. Delete the folder to start over.

## Index the libraries you use

From inside your Python project:

```bash
grepwise deps          # your dependencies, their exact versions (from uv.lock, poetry.lock, ...) and what's indexed
grepwise sync          # index every direct dependency at its locked version
grepwise add typer     # one library, at the version your project locks (or the latest)
grepwise add typer==0.27.2
grepwise add https://github.com/pallets/click --ref 8.5.0   # any GitHub repo at a tag
grepwise add ~/code/internal-lib                             # a folder on your machine
grepwise sources       # what's indexed
grepwise remove typer@0.27.2
```

Grepwise finds each package's GitHub repo from PyPI and the git tag for that exact version; if there's no matching tag it says so instead of guessing. Code is split by function and class, docs (Markdown, MDX, reStructuredText) by heading. Re-adding a version re-indexes it in place, reusing every embedding already made.

Until Grepwise installs as a tool (N4), run these from your project folder as `uv run --project /path/to/grepwise/backend grepwise ...`. Use `--project`, not `--directory`: `--directory` switches into `backend/`, so Grepwise would read its own dependencies instead of yours.

## Use it in Claude Code / Cursor

Grepwise gives your editor's model three tools; it makes no LLM calls itself.

- **`search`**: the real code and docs of your project's libraries, by plain name (`typer` means the version your lockfile pins). A dependency that isn't indexed yet is indexed on the spot: keyword results within seconds, semantic search filling in behind.
- **`read`**: exact, numbered lines by citation or path. It also checks a citation is real.
- **`libraries`**: your dependencies, their locked versions, and what's indexed.

Every result has a citation (`typer@0.27.2:docs/tutorial/options/required.md:L1-L74`) and a link to those exact lines on GitHub (or in a local file) that you can click.

**Claude Code** (use `--project`, not `--directory`, so Grepwise sees the project you're working in):

```bash
claude mcp add --scope user grepwise -- uv run --project /path/to/grepwise/backend grepwise mcp
```

**Cursor** (`~/.cursor/mcp.json`) or **Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "grepwise": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/grepwise/backend", "grepwise", "mcp"]
    }
  }
}
```

Then just ask, e.g. *"How do I make an option required in Typer?"* The server tells the model to look libraries up instead of answering from memory. There's also an `ask` prompt (in Claude Code: `/mcp__grepwise__ask`).

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
