# N5: Prove it, then show it

Part of [10-v2-plan.md](10-v2-plan.md). **Goal:** measured evidence of whether Grepwise helps, reported honestly, and a README that shows it.

## 1. Claude Code with vs. without Grepwise (`grepwise eval answers`)

- Every eval question is asked through headless Claude Code (`claude -p`, model pinned to `sonnet`), in an empty folder, twice:
  - **without**: web search and fetch, which is what Claude Code can look up today;
  - **with**: the same, plus Grepwise's MCP server.
- Identical otherwise: the same prompt (library and exact version, then the question), file, shell and editing tools blocked, `--strict-mcp-config` so the user's own MCP servers can't leak in, no saved sessions.
- **Scored automatically:** fact coverage (expected phrases mentioned), file hit (an expected file referenced as a citation or link; every file for `paths_all`), honesty on `absent` questions (says it isn't there), plus usage cost, time and turns from Claude's JSON output.
- Saved per question (`evals/runs/answers/<condition>/<id>.json`): a stopped run resumes, and failed runs are retried.
- **Uses the user's Claude plan.** Run a small trial first (`--ids` / `--limit`) and check the cost before a full run.

## 2. Resolution accuracy (`grepwise eval resolution`)

`evals/resolution.yaml`: 33 popular packages, each version pinned (latest on PyPI on 2026-10-10), with the expected GitHub repo written by hand. Resolves each without the cache; counts the right repo and an exact-version tag.

## 3. README

A short, conventional README: what it is, install, usage, how it works, one benchmark table, development. **Decided 2026-10-11:** no demo video and no case studies in the repo. The two case-study sessions were run (and caught a search-default problem, see below) but aren't kept as docs; the eval numbers are the evidence.

## 4. Acceptance

- [ ] Offline tests for both evals (fake runner, fake resolver).
- [ ] Resolution eval run and reported.
- [ ] Answers trial run; full run (or a stated subset) after checking cost with Aditya.
- [ ] README rewritten around the measured results.

## 5. Results (2026-10-10)

**Answers** (56 questions × 2, Sonnet, 112 runs, $7.16 of usage in total):

| | without | with |
|---|---|---|
| Fact coverage (51 answerable) | 0.97 | 0.98 |
| File cited | 0.73 | **0.96** |
| File cited, by category: api / code / cross / docs | 1.00 / 0.95 / 0.30 / 0.62 | 1.00 / 1.00 / 1.00 / 0.88 |
| Honest on absent (5) | 1.00 | 1.00 |
| Used Grepwise / used the web | 0% / 86% | 100% / 0% |
| Seconds per answer | 19.2 | **10.3** |
| Turns | 4.3 | 3.2 |
| Usage per answer | $0.059 | $0.069 |

- Facts are a tie: with web access, Claude Code finds the files at the exact tag on GitHub. Grepwise wins on grounding (citations) and speed, for slightly more usage.
- The one fact miss "with" (code-20) is a correct answer that says "vendored copy" instead of the checked phrase `_click`; the expected facts were left unchanged.
- 6 of 56 "without" answers used no tool at all (answered from memory).

**Resolution:** 31/33 with the original tag patterns (polars `py-2.0.0`, alembic `rel_1_20_0` failed, each with the right tag as the closest suggestion); 33/33 after adding `py-` and `rel_X_Y_Z` as general patterns.

**Case-study sessions** (headless Claude Code with Grepwise, not kept as docs): Typer 0.27.2 `--version` + password prompt (code written, then run and checked by hand: works) and itsdangerous key rotation (one search, 17 s, every claim linked).

**Found and fixed along the way:**
- The trial showed "with" could fail on `typer@0.27.2` when the source is named `fastapi/typer`: plain names now match the repo part of `owner/repo`, and a version can be the tag.
- The first full run let the baseline use file tools; in an empty folder they went looking around the disk, and macOS asked for access to other apps' data. The run was stopped and restarted with web-only baseline tools; the 30 answers from the first setup were discarded.
- The honesty check missed "has no …"; saved answers are now rescored on load, so scoring fixes don't need new runs.
- The Typer case study's first search mixed in Click results: with a project, default search now covers only its dependencies.
- A second first-open race (switching a new database to WAL can fail at once with "locked"): retried; 0 failures in 30 runs of 8 concurrent openers.
