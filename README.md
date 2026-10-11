# Grepwise

An MCP server that gives your coding assistant the real source code and docs of your project's dependencies, at the exact versions in your lockfile, with a citation and a link for every result.

- **Exact versions:** reads `uv.lock`, `poetry.lock`, pinned requirements or `.venv`.
- **No setup per library:** a dependency is indexed the first time it's searched.
- **Code and docs:** code split by function and class, docs (Markdown, MDX, reStructuredText) by heading.
- **Local:** SQLite and a local embedding model. No Docker, no API keys.

## Install

Requires [uv](https://docs.astral.sh/uv/) and git.

```bash
git clone <repo-url> grepwise && cd grepwise
make install       # installs the `grepwise` command
grepwise setup     # downloads the model, registers the MCP server with Claude Code
```

Use `grepwise setup --client cursor` or `--client claude-desktop` for other editors, and `grepwise doctor` to check the installation.

## Usage

Restart Claude Code and ask about a library your project uses. The server provides three tools:

| Tool | Description |
|---|---|
| `search` | Search a library's code and docs by name (`typer` means the version your project pins) |
| `read` | Read exact lines by citation or path |
| `libraries` | List the project's dependencies and what's indexed |

Results look like `typer@0.27.2:docs/tutorial/options/version.md:L1-L112`, with a link to those lines on GitHub at that tag.

The same index is available from the terminal:

```bash
grepwise deps                        # dependencies and locked versions
grepwise sync                        # index all direct dependencies
grepwise add typer==0.27.2           # a package, a GitHub URL (--ref <tag>), or a local folder
grepwise search "make an option required"
grepwise sources                     # list indexed sources
grepwise gc                          # remove sources unused for 30 days
```

Data is stored in `~/.grepwise`.

## How it works

1. **Resolve:** the package's GitHub repo comes from PyPI, and the tag for the locked version from `git ls-remote`. If no tag matches, it reports the closest ones rather than guessing.
2. **Index:** shallow clone, split into chunks, stored in SQLite with an FTS5 keyword index. Embeddings are computed in the background.
3. **Search:** BM25 keyword ranking, vector similarity and exact symbol matches, merged with reciprocal rank fusion.

## Benchmarks

56 questions about Typer, Click, itsdangerous and an OpenAPI spec, asked through Claude Code (Sonnet) with web search only vs. with Grepwise:

| | Web only | Grepwise |
|---|---|---|
| Expected facts in answer | 97% | 98% |
| Cites the source file | 73% | 96% |
| Time per answer | 19.2 s | 10.3 s |

Search finds the right file in the top 5 for 88% of questions; package resolution is correct for 33/33 popular packages. Method and details: [docs/spec](docs/spec/15-n5-prove-it.md).

## Development

```bash
make setup     # dev environment in .venv
make test
make lint
```

Python projects and GitHub-hosted packages only, for now.
