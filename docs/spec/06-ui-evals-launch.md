# M6: Web UI, evals, deployment and launch

**Goal:** a public, polished Grepwise: a web UI anyone can try, measured quality numbers, a live deployment on free hosting, a PyPI release for the MCP server, and a README that shows it all.

M6 has **four phases**, built and reviewed one at a time:

| Phase | What | Rough effort |
|---|---|---|
| **6.1** | Web UI (Next.js) and the small API additions it needs | 1 day |
| **6.2** | Evals: retrieval and answer quality, Gemini vs. local, with vs. without MCP | 1 day (half of it is you writing questions) |
| **6.3** | Deployment: Neon, Render, Vercel, GitHub Actions | 0.5–1 day |
| **6.4** | Launch: README, demo video, PyPI, licence, final checks | 0.5 day |

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

# Phase 6.3: Deployment

## 10. Database: Neon
- Create a Neon project, enable `vector`, run `grepwise db migrate` against it.
- Index the demo sources **from your laptop** (`DATABASE_URL=<neon>` with the local embedder): itsdangerous, Typer (docs and code), Petstore, plus one or two popular libraries if storage allows.
- Check the size stays well under **0.5 GB** (`grepwise stats`). Record it in the README.

## 11. Backend: Render (free web service)
- `backend/Dockerfile`, multi-stage with uv:
  - The **embedding model and tree-sitter grammars are baked into the image** at build time, so a cold start never downloads them.
  - Runs as a non-root user; `uvicorn` on `$PORT`; a health check on `/health`.
- **Memory:** must run within **512 MB**. Test locally with `docker run --memory=512m`, then ask a question and run an MCP call; record peak memory.
- Environment (set in Render, never in the repo): `DATABASE_URL`, `GEMINI_API_KEY`, `LLM_PROVIDER=gemini`, `EMBEDDER=local`, `TRUST_PROXY_HEADERS=true`, `ALLOWED_ORIGINS=<vercel url>,https://grepwise.adityakhandelwal.me`, `MCP_ALLOWED_HOSTS=<render host>`, `HOSTED_MAX_STEPS=5`, `CHAT_GLOBAL_PER_DAY` (set from flash-lite's real limit), and `MCP_API_KEYS` (optional).
- Check how Render sets `X-Forwarded-For`, and confirm the right-most rule holds (log one request's header in a test deploy, then remove the log).

## 12. Frontend: Vercel (Hobby)
- Import `web/` as a project; set `NEXT_PUBLIC_API_URL` to the Render URL.
- **Custom domain:** `grepwise.adityakhandelwal.me`, via a CNAME record you add at your DNS provider.

## 13. GitHub Actions
- **CI** (`ci.yml`, on push and PR): backend lint and tests (with a Postgres + pgvector service container, and **`GREPWISE_REQUIRE_DB=1`** so database tests fail instead of silently skipping if the service isn't reachable), and frontend lint, typecheck, tests and build.
- **Indexing** (`index.yml`, `workflow_dispatch` only): inputs `type` (repo/docs/openapi), `url`, `ref`/`version`, `path`, `name`. Runs the matching `grepwise index*` command against Neon using a `NEON_DATABASE_URL` secret, and caches the embedding model between runs.
  - **Only you can trigger it.** Indexing repos that visitors submit is a non-goal: it would need abuse protection and storage controls that don't fit the free tier.
- Optional `keepwarm.yml`: ping `/health` during the day if you decide it's acceptable under Render's terms. Off by default.

## 14. Acceptance (6.3)
- [ ] The live UI answers a question end to end, with green citations, on the custom domain.
- [ ] Remote MCP works: `claude mcp add --transport http grepwise https://<render-host>/mcp`.
- [ ] The cold start shows the wake-up message and then works.
- [ ] The rate limits and the site cap work on the live service (test with low temporary values).
- [ ] CI is green; the indexing workflow runs once successfully.
- [ ] No secrets in the repo or its git history (`git log -p | grep -i "key"` reviewed), and Render logs don't show keys or full error bodies.

---

# Phase 6.4: Launch

## 15. README (the main showcase)
In this order:
1. **One-line pitch**, then a **demo GIF** (about 15 s).
2. **Try it:** live URL, plus the MCP one-liner.
3. **What makes it different:** checked citations (real *and* seen), hybrid search, code + docs + API in one place, local-first (fully offline with Ollama), and MCP.
4. **Architecture diagram** (Mermaid, which GitHub renders).
5. **Results:** the eval tables from 6.2 (retrieval ablation, answers per provider, and the MCP comparison), with a one-line explanation of each metric.
6. **Quickstart:** local setup, indexing, asking, MCP.
7. **Design decisions and trade-offs:** local embeddings (the quota story), hybrid search with RRF, the citation checks, the tool layer, free hosting.
8. **Limitations** (honest): `find_symbol` misses methods inside small classes; MDX headings inside JSX blocks; free-tier daily caps; the local model is weaker than Gemini; English-centric keyword search.
9. **Roadmap:** a symbols table, more languages, reranking, user-submitted repos.

## 16. Demo video (60–90 s)
A script in `docs/demo-script.md`: ask a cross-source question → tool trail → streamed answer → click a citation → the verified lines → the same question in Claude Code through MCP → the eval table. Record with any screen recorder; link it at the top of the README.

## 17. PyPI release
- Package `grepwise`, version `0.1.0`. **Move `backend/migrations/` into the package** (e.g. `src/grepwise/migrations/`) so it ships with the install; update the migration runner and tests.
- Publish from GitHub Actions on a version tag, using **PyPI trusted publishing** (no API token stored).
- `uvx grepwise mcp` documented: it needs a reachable Postgres with pgvector (`DATABASE_URL`) and indexed sources, or use the hosted `/mcp` instead.
- Check the package installs and runs `grepwise --help` in a clean virtual environment.

## 18. Final polish
- `LICENSE` (MIT), and a privacy note on `/about`: questions are logged for quality evaluation; your own Gemini key is never stored.
- Remove debug code, check every `TODO`, and make the `.env.example` comments match the final settings.
- The resume bullet, written with the **real** eval numbers.

## 19. Acceptance (6.4)
- [ ] The README renders correctly on GitHub, with the GIF, diagram and tables.
- [ ] `uvx grepwise --help` works from PyPI in a clean environment.
- [ ] The demo video is recorded and linked.
- [ ] A final code review across the whole project.
- [ ] **The full recap and quiz.**

---

## Non-goals (whole of M6)
- User accounts, saved conversations, user-submitted repos
- Paid hosting or paid LLM tiers
- Private repos
