# 17: Does Grepwise add value over Claude reading `.venv`?

Written 2026-10-12. The N5 eval compared Grepwise with web search in an empty folder. In a real project, Claude Code can also read the installed packages in `.venv`, which hold the exact installed source. This test asks whether Grepwise still helps when that's possible.

## Conditions (`grepwise eval answers --condition files-all`)

All three run headless Claude Code (`claude -p`, Sonnet) in a project folder with Typer, Click, itsdangerous, httpx, tenacity and attrs installed in `.venv`. File tools (Read, Grep, Glob) and web search are allowed; shell and editing are blocked.

| Condition | Adds |
|---|---|
| `files` | nothing |
| `files-hint` | a one-line CLAUDE.md: "library source is in `.venv`; read it instead of answering from memory" |
| `files-grepwise` | Grepwise, and a one-line CLAUDE.md: "search libraries with Grepwise first" |

- **`--setting-sources project`**: only the project's CLAUDE.md applies, not the user's global one.
- **Safety:** the projects live in the temp folder, and file tools are denied the home folder (`Read(~/**)`, `//Users/**`, `//Volumes/**`). A search can't wander into macOS-protected folders.

## Questions

- The 50 eval questions about installed libraries. The Petstore API can't be installed, so its 6 questions are left out.
- 12 new questions about httpx, tenacity and attrs (`evals/questions_unindexed.yaml`), which Grepwise hasn't indexed. The first question about each one tests first use: indexing on the spot, keyword-only results until embeddings fill in.
- Scored as in N5. A path inside `.venv` counts for the repo path without `src/`.

## Trial (2026-10-12, 4 questions × 3)

| | files | files-hint | files-grepwise |
|---|---|---|---|
| Facts / file cited | 100% / 67% | 100% / 67% | 100% / 67% |
| Seconds / turns | 15 / 4.5 | 12 / 3.5 | 37 / 2.8 |

- Claude read `.venv` on its own even without the hint.
- Grepwise's time was inflated in two ways:
  - two other sessions were embedding numpy and mcp, using about 6 cores;
  - every headless run starts a new Grepwise server, which happens once per session in real use.

## Status

The full run (about 186 runs, about $9) is on hold. Aditya is testing Grepwise in real work for a few days instead. The harness stays for later.
