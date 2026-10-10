# Grepwise

Give your coding assistant the real docs and source of the libraries you use, at the exact version, with line-level citations it can check. Grepwise is an MCP server that runs on your machine: no Docker, no API keys.

**Status:** v2 in progress ([plan](docs/spec/10-v2-plan.md)). N1 done: SQLite storage, no LLM or server needed. N2 done: index your project's dependencies by name. N3 done: three MCP tools, libraries indexed on first use. N4 done: `make install` and `grepwise setup`. The earlier web UI and built-in agent are archived at the `v0.1-webapp` tag.

## Install

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`) and git.

```bash
git clone <this repo> && cd grepwise
make install      # puts `grepwise` on your PATH, pinned to the tested versions
grepwise setup    # downloads the embedding model, registers Grepwise with Claude Code for every project
```

`grepwise setup --client cursor` or `--client claude-desktop` registers it there instead; `--print` only shows the config. `grepwise doctor` checks everything and says how to fix what's wrong.

Everything Grepwise stores lives in `~/.grepwise/`: the database (`grepwise.db`) and the embedding model. `grepwise gc` removes libraries you haven't searched in 30 days; deleting the folder starts over.

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


## Use it in Claude Code / Cursor

Grepwise gives your editor's model three tools; it makes no LLM calls itself.

- **`search`**: the real code and docs of your project's libraries, by plain name (`typer` means the version your lockfile pins). A dependency that isn't indexed yet is indexed on the spot: keyword results within seconds, semantic search filling in behind.
- **`read`**: exact, numbered lines by citation or path. It also checks a citation is real.
- **`libraries`**: your dependencies, their locked versions, and what's indexed.

Every result has a citation (`typer@0.27.2:docs/tutorial/options/required.md:L1-L74`) and a link to those exact lines on GitHub (or in a local file) that you can click.

`grepwise setup` registers the server for you. To do it by hand, point your editor at `grepwise mcp`:

```json
{ "mcpServers": { "grepwise": { "command": "/absolute/path/to/grepwise", "args": ["mcp"] } } }
```

Then just ask, e.g. *"How do I make an option required in Typer?"* The server tells the model to look libraries up instead of answering from memory. There's also an `ask` prompt (in Claude Code: `/mcp__grepwise__ask`).

## The same tools from the terminal

```bash
grepwise search "how do I make an option required?"
grepwise search "find pet by id" --source petstore --kind endpoint
grepwise symbol TimestampSigner
grepwise read pallets/itsdangerous@2.2.0 src/itsdangerous/timed.py --lines 20-60
grepwise endpoint "GET /pet/42" --source petstore
grepwise verify "petstore@1.0.27:GET /pet/{petId}:L1-L27"
grepwise tool search '{"query": "required option", "limit": 3}'  # raw JSON, as a model sees it
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
make setup  # dependencies, grammars and the embedding model, for working on the code (backend/.venv)
make test   # offline: each test gets its own temporary database
make lint   # ruff + mypy
```
