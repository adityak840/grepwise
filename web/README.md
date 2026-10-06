# Grepwise web UI

Next.js (App Router), TypeScript, Tailwind CSS and shadcn/ui. Talks to the FastAPI backend in
`../backend`.

```bash
cp .env.example .env.local     # NEXT_PUBLIC_API_URL=http://localhost:8000
npm install
npm run dev                    # http://localhost:3000 (start the backend with `make serve`)
```

Checks: `npm run lint`, `npm run typecheck`, `npm test` (Vitest), `npm run build`.

The core logic lives in plain, tested modules under `src/lib/`: `sse.ts` (parsing the `/chat`
stream), `answer.ts` (the answer state and citation chips), `github.ts` and `errors.ts`.
