# M6: Web UI, evals and launch

> **Phases 6.2–6.4 are superseded** by the v2 plan, [10-v2-plan.md](10-v2-plan.md) (2026-10-10). Phase 6.1 was built and is archived at the `v0.1-webapp` tag.

**Goal:** a polished, local-first Grepwise: a web UI, measured quality numbers, easy MCP setup, and a GitHub README that shows it all. There is no public deployment (see phase 6.3).

M6 has **four phases**, built and reviewed one at a time:

| Phase | What | Rough effort |
|---|---|---|
| **6.1** | Web UI (Next.js) and the small API additions it needs | 1 day |
| **6.2** | Evals: retrieval and answer quality, Gemini vs. local, with vs. without MCP | 1 day (half of it is you writing questions) |
| **6.3** | MCP-first polish: `mcp-config`, `doctor`, `remove`, demo sources, CI | 0.5–1 day |
| **6.4** | README, demo video, MCP case studies, licence, final checks | 1 day |

Each phase has its own acceptance criteria, and ends with a code review before the next one starts.

---

# Phase 6.1: Web UI

## 1. Stack and layout
- `web/`: **Next.js (App Router), TypeScript, Tailwind CSS, shadcn/ui**. Your strongest area, so make it polished.
- Markdown answers rendered with `react-markdown`, and code highlighted with a lightweight highlighter (e.g. Shiki).
- `NEXT_PUBLIC_API_URL` points at the FastAPI backend (local: `http://localhost:8000`).
- Tests: Vitest + React Testing Library.

## 2. Screens

| Route | Content |
|---|---|
| `/` **Ask** | Question box, source picker, streaming answer, tool trail, citations, citation viewer |
| `/sources` | Indexed sources: type, version, documents, chunks, status |
| `/evals` | Results from 6.2: retrieval and answer metrics per provider, with a short explanation of each metric |
| `/about` | What Grepwise is, how it works (architecture diagram), MCP setup, privacy note, GitHub link |

## 3. The Ask screen

**Streaming (the core):**
- `POST /chat` with `fetch`, reading the response body as a stream and parsing SSE frames by hand (`EventSource` only supports GET). One `parseSSE()` function, unit-tested.
- Event handling:
  - `step` and `tool_result`: a **tool trail** showing each call and its summary ("→ search 'required option': 6 results"). Collapsed by default once the answer starts.
  - `token`: append to the answer.
  - **`reset`: clear the answer text so far** (the model was thinking aloud before a tool call). Show a subtle "looking something up…" state.
  - `citations`: replace each `[[…]]` marker in the answer with a numbered **citation chip**: green for `verified`, struck-through red for the others, with the reason on hover.
  - `done`: footer with steps, time, provider and model, plus a warning if `grounded` is false.
  - `error`: a friendly message per code (`daily_limit`, `rate_limited`, `quota_exhausted`, `timeout`…).
- A **Stop** button aborts the request; the backend stops the run on disconnect.

**Citation viewer:**
- Clicking a chip opens a side panel (a bottom sheet on mobile) showing the **verified lines** from the `citations` event, with line numbers starting at the cited line, the citation string, and a copy button.
- "Show more context" loads ±20 lines through the new `/api/read` endpoint.
- For GitHub repos at a tag, an "Open on GitHub" link (`https://github.com/<name>/blob/<version>/<path>#L<start>-L<end>`).

**Other:**
- Source picker: multi-select from `/api/sources`; none selected means all.
- Follow-up questions: the client keeps the conversation and sends it as `history` (the server stores none).
- **Cold start:** on page load, call `/health`. If it doesn't answer within about 2 s, show "Waking up the server (free hosting sleeps when idle)…" until it does.
- **Use your own Gemini key** (optional, in settings): kept in `sessionStorage` only, sent as an `X-Gemini-Key` header. A short note explains it's used for that request only and never stored by Grepwise.
- Example questions as clickable chips, for first-time visitors.
- **Question history:** past questions saved in the browser's `localStorage` only (at most 30, newest first, deduplicated), shown as "Your recent questions" on the start screen and in a History menu. Answers aren't saved. Remove one, or clear all.
- **Smooth streaming:** tokens are rendered at most once per animation frame; code blocks are syntax-highlighted only once the answer is complete; the view follows new text only while the reader is at the bottom (scrolling up stops it); the tool trail's collapse is animated.
- **Tabs:** the conversation lives above the pages (a provider in the layout), so switching tabs keeps it, even while an answer streams; each tab fades in (`app/template.tsx`), respecting reduced motion.
- Responsive down to 360 px, light and dark mode, keyboard accessible, `prefers-reduced-motion` respected.

## 4. Backend additions (`api/app.py`)
- `GET /api/sources` returns `list_sources` output.
- `GET /api/read?source=&path=&start=&end=` returns `read_file` output (via `call_tool`).
- `GET /api/evals` returns the latest eval report (from 6.2), or 404 if there isn't one.
- These three are read-only, use the `/mcp` anonymous per-IP limits, and are included in CORS.
- **`X-Gemini-Key` on `/chat`:**
  - Used to create that request's LLM client only, with `LLM_PROVIDER=gemini`.
  - **Never logged, never stored, never written to `agent_runs`** (record `byo_key: true` instead, via a new boolean column in migration 007).
  - Requests with their own key **skip the site-wide daily cap** but still count against the per-IP limits.
  - A rejected key returns a clear `invalid_key` error.
- `HOSTED_MAX_STEPS` (default 5) applies to `/chat`; the CLI keeps `AGENT_MAX_STEPS`.

## 5. Acceptance (6.1)
- [ ] Vitest: `parseSSE` (split frames, multi-line data, unknown events); the answer reducer (tokens, `reset`, citations → chips with the right status); error-code messages; the citation-to-GitHub URL builder (including bracketed paths).
- [ ] Backend tests: the three `/api` endpoints; `X-Gemini-Key` used, skips the site cap, and never appears in logs or `agent_runs`; `HOSTED_MAX_STEPS`.
- [ ] Manual, against the local backend: ask the three M4 questions; watch the tool trail, the streamed answer, and green chips; open the viewer; load more context; stop mid-answer; trigger `reset` (Ollama often thinks aloud); a phone-sized window; dark mode.
- [ ] `npm run lint`, `npm run typecheck`, `npm test` and `npm run build` all pass.

---

# Phase 6.2: Evals

## 6. Eval set (`evals/questions.yaml`), written by you
Pinned sources: `pallets/itsdangerous@2.2.0` (code), `fastapi/typer@0.27.2` (docs, plus code for cross-source questions), and `petstore@1.0.27` (API).

```yaml
- id: code-07
  category: code            # code | docs | api | cross
  question: How does TimestampSigner decide that a signature has expired?
  sources: [pallets/itsdangerous@2.2.0]
  expect:
    paths: [src/itsdangerous/timed.py]   # any of these cited, or in the top 5 search results
    symbols: [TimestampSigner]            # optional
    facts: [max_age, SignatureExpired]    # phrases a correct answer must mention
```
- **`expect.paths_all`** (cross-source questions): every listed path must be cited, not just one, e.g. a docs page *and* the example code it includes. Citation hit for these = all paths cited.
- `draft: true` marks a question not yet checked by you; the runner skips drafts unless `--include-drafts` is passed.
- Index **Typer's repo code** too (`grepwise index https://github.com/fastapi/typer --ref 0.27.2`): its docs include code examples from `docs_src/` by reference (`{* docs_src/... *}`), so the code lives only in the repo, which makes natural cross-source questions.
- About **20 code, 15 docs, 5 API and 10 cross-source** questions. A starter file with 6 checked drafts is in `evals/questions.yaml`.
- **Write them yourself**, by reading the sources. Generated questions tend to be easy for the system that generated them. Claude can suggest candidates, but you check each one against the actual files and write the expected paths and facts.
- Include a few **"not in the sources"** questions (`expect: {absent: true}`), where the right answer is an honest "not found".

## 7. What gets measured

**A. Retrieval (no LLM, free, fast):** run the `search` tool on each question.
- **Hit@5:** is an expected path in the top 5?
- **MRR:** how high the first expected path ranks.
- **Comparison ("ablation"):** **vector-only vs. keyword-only vs. hybrid**, by switching one side of hybrid search off with a flag. This shows *why* hybrid search is used, with numbers.

**B. Answers (the agent, per provider):**
- **Citation hit:** a `verified` citation points to an expected path.
- **Fact coverage:** the share of expected facts mentioned (case-insensitive phrase match).
- **Grounded rate:** answers with at least one verified citation.
- **Dropped citations:** the share of citations that failed checks (made up, unseen, bad range).
- **Honesty:** for `absent` questions, did it say it couldn't find the answer?
- Average steps, time and tokens.
- **Optional `--judge`:** an LLM grades each answer against the expected facts (1–5, with a reason). Off by default (it uses quota); reported separately.

**Providers:** `gemini-3.5-flash-lite`, `grepwise-qwen3.5` (thinking off), and optionally `gemma4:12b`.

**C. With vs. without Grepwise in Claude Code (manual, small):** the 10 cross-source questions, asked in Claude Code once **without** and once **with** the Grepwise MCP server. You score each answer against the expected facts in `evals/mcp_comparison.csv`. Report it honestly as a small manual study.

## 8. Runner (`grepwise eval`)
- `grepwise eval retrieval [--mode hybrid|vector|keyword]`
- `grepwise eval answers --provider gemini|ollama [--model m] [--limit n] [--judge]`
- **Resumable:** results saved per question (`evals/runs/<run-id>/<question-id>.json`). A re-run skips finished questions, so a quota limit only pauses the run, and it can continue tomorrow.
- **Paced** to stay under the provider's per-minute limit.
- `grepwise eval report` writes `evals/results/latest.json` and `evals/results/REPORT.md` (tables per category and provider), which feed `/api/evals` and the README.
- `make eval-retrieval` runs offline and is cheap; it could also run in CI.

## 9. Acceptance (6.2)
- [ ] The runner is tested with ScriptedClient and FakeEmbedder: metrics are computed correctly on a tiny fixture set; resume skips finished questions; `absent` honesty is scored.
- [ ] A real retrieval run (all three modes) and real answer runs for Gemini and Ollama are completed; the report is generated.
- [ ] The manual MCP comparison is filled in.
- [ ] Numbers sanity-checked: open 5 random results and confirm the scoring was fair.

---

# Phase 6.3: MCP-first polish and CI

**Decision (2026-10-10): no public deployment.** Grepwise is used where it's useful: on a developer's own machine, through MCP in their editor, over the repos and docs they choose. A hosted demo would only answer about sources we picked, and it would need free-tier workarounds (memory, storage, rate limits, abuse protection) that solve no real problem. The showcase is the **GitHub repo**: the README, a demo video, real MCP sessions and eval numbers. The hosted-only code already built (rate limits, `X-Gemini-Key`, MCP over HTTP) stays as it is but isn't extended.

## 10. The web UI is frozen
**MCP is the product; the web UI is a side piece.** The UI built in 6.1 stays as it is (it works, and it's useful for showing citations visually), but it gets no new features. The README presents it as an optional extra, after the MCP path. Only one fix:
- **API unreachable:** replace the "Waking up the server (free hosting sleeps when idle)" message with "Can't reach the Grepwise API at `<url>`. Start it with `make serve`." and a Retry button. Drop the hosted-service privacy wording on `/about`.

## 11. Managing sources from the CLI
MCP users manage sources in the terminal, so the CLI covers the whole lifecycle:
- **`grepwise remove <name@version> [--type repo|docs|openapi] [--yes]`** deletes a source and its documents and chunks (check the schema cascades; fix it in migration 008 if not). The embedding cache is kept, so re-indexing is fast. It asks for confirmation unless `--yes`; when a name and version match more than one source type (e.g. Typer's docs and code), it requires `--type`.
- Re-running `grepwise index …` for an existing `name@version` already re-indexes in place; document that in `--help` and the README.
- The MCP tools stay read-only: indexing and removal are deliberate CLI actions.

## 12. One-command setup and MCP onboarding
- **`make demo-sources`** indexes the pinned eval sources (itsdangerous 2.2.0, Typer 0.27.2 docs and code, Click 8.5.0 docs and code, Petstore 1.0.27), so anyone can reproduce the eval numbers.
- **`grepwise mcp-config [--client claude-code|cursor|claude-desktop]`** prints the exact `claude mcp add …` command or the JSON block, with the absolute path to this checkout's `backend/` filled in. The README uses it instead of asking people to edit paths by hand.
- `grepwise doctor`: checks that the database is reachable and migrated, the pgvector extension is present, the embedding model is downloaded, at least one source is `ready`, and (if set) the Gemini key or Ollama model is reachable. It prints one ✓/✗ line per check with a fix hint. This is the first thing to run when the MCP server "doesn't work".

## 13. GitHub Actions: CI only
- **`ci.yml`** (on push and PR): backend lint and tests (with a Postgres + pgvector service container, and **`GREPWISE_REQUIRE_DB=1`** so database tests fail instead of silently skipping if the service isn't reachable), and frontend lint, typecheck, tests and build.
- Cache uv and npm downloads. Tests stay offline (FakeEmbedder; no model download, no Gemini).
- A CI status badge for the README.

## 14. Acceptance (6.3)
- [ ] Backend tests: `remove` deletes documents and chunks but keeps the embedding cache, refuses an ambiguous name without `--type`, and asks for confirmation without `--yes`; `mcp-config` output for each client; `doctor` reports a missing database clearly.
- [ ] Vitest: the new "can't reach the API" message.
- [ ] Manual: index a small repo, ask about it from Claude Code over MCP, then `grepwise remove` it.
- [ ] Manual: on a fresh clone, follow the README's setup, `make demo-sources`, and connect Claude Code using `grepwise mcp-config`.
- [ ] CI is green on GitHub.

---

# Phase 6.4: README, demo and MCP case studies

The resume links to the GitHub repo. The README must make sense to someone who never runs it, and get someone who does want to use it working in Claude Code in about 10 minutes.

## 15. README (the whole showcase)
In this order:
1. **One-line pitch**, a CI badge, then a **demo GIF** (about 15 s): a question in Claude Code, Grepwise tool calls, an answer with citations.
2. **Why:** coding assistants guess at library APIs from training data that may be out of date. Grepwise gives them the real code and docs at the exact version you use, and every claim comes with a line-level citation that is checked.
3. **Use it in Claude Code** (the main path): clone → `make setup db migrate` → index what you work with (`grepwise index …`) → `grepwise mcp-config` → ask in Claude Code. `grepwise sources` and `grepwise remove` to manage what's indexed. Then `grepwise doctor` if anything fails.
4. **Case studies** (section 17): two or three real tasks solved with Grepwise over MCP, each a short summary linking to the full transcript.
5. **How it works:** an **architecture diagram** (Mermaid, which GitHub renders): ingest (tree-sitter, docs and OpenAPI splitters) → Postgres (pgvector + full-text) → hybrid search with RRF → one tool layer → the MCP server and the web agent; then the citation checks (real *and* seen).
6. **Results:** the eval tables from 6.2 (retrieval ablation, answers per provider, the with/without MCP comparison), with a one-line explanation of each metric and how to reproduce them (`make demo-sources`, `grepwise eval …`).
7. **Optional: the web UI**, kept short: one screenshot of an answer with citation chips and the viewer, and the two commands to run it. It's a visual way to inspect answers, not the main way to use Grepwise.
8. **Design decisions and trade-offs:** local embeddings (the quota story), hybrid search with RRF, citations that must be seen and real, the shared tool layer, read-only tools, why there is no hosted version.
9. **Limitations** (honest): `find_symbol` misses methods inside small classes; MDX headings inside JSX blocks; the local model is weaker than Gemini; English-centric keyword search; indexing and removing sources are CLI-only.
10. **Roadmap:** a symbols table, more languages, reranking, an optional `index` MCP tool, a PyPI package.
- A note that the eval questions were drafted with Claude and checked by you.

## 16. Demo video (60–90 s)
A script in `docs/demo-script.md`: in Claude Code, ask a question about a library whose current API differs from what models remember → Grepwise tool calls → the answer with citations → open a cited file at those lines → the same question without Grepwise (a vaguer or outdated answer) → the eval table. (The web UI can appear for a few seconds at most.) Record with any screen recorder; upload as an unlisted YouTube video or a GitHub-hosted MP4, and link it at the top of the README. The GIF is a 15 s cut of the same recording.

## 17. MCP case studies (`docs/case-studies/`)
Real tasks, done in Claude Code with Grepwise connected, that show it solving the kind of problem it's for. For each one, write a Markdown page with: the task, the sources indexed, the prompt, a trimmed transcript (the tool calls and the answer with its citations), whether the citations verified, and what would have gone wrong without it. Candidates (pick two or three, done for real):
- **Write correct code against a pinned version:** "Add a `--version` flag and a password prompt to this Typer CLI", using Typer 0.27.2's docs *and* `docs_src` examples.
- **Explain behaviour from the source:** "Why does my itsdangerous token fail after rotating keys?" (key rotation in `signer.py`).
- **Check an API before calling it:** "Write a client function that lists available pets", grounded in the Petstore spec's parameters and error codes.
- **Your own work repo** (only public, shareable code): index it and ask how a feature works.

## 18. Final polish
- `LICENSE` (MIT).
- Remove debug code, check every `TODO`, and make the `.env.example` comments match the final settings (mark the hosted-only ones as such).
- Update `docs/spec/00-overview.md` if anything drifted.
- The resume bullet, written with the **real** eval numbers and the GitHub link.

## 19. Acceptance (6.4)
- [ ] The README renders correctly on GitHub, with the GIF, diagram, tables and screenshots, and every command in it works on a fresh clone.
- [ ] Two or three case studies are written from real sessions.
- [ ] The demo video is recorded and linked.
- [ ] A final code review across the whole project.
- [ ] **The full recap and quiz.**

---

## Non-goals (whole of M6)
- A public deployment (Vercel, Render, Neon), and anything that only exists for one: visitor-submitted repos, abuse protection, wake-up handling
- A PyPI release (on the roadmap; it would still need a local Postgres)
- User accounts, saved conversations
- Paid LLM tiers
- Private repos
- Indexing from MCP (the tools stay read-only; indexing is a deliberate CLI or UI action)
