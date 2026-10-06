import type { Metadata } from "next";

export const metadata: Metadata = { title: "About" };

const GITHUB_URL = process.env.NEXT_PUBLIC_GITHUB_URL;

function Box({ title, detail, accent }: { title: string; detail: string; accent?: boolean }) {
  return (
    <div className={`rounded-lg border p-3 text-center ${accent ? "border-emerald-600/40 bg-emerald-500/5" : "bg-muted/40"}`}>
      <div className="text-sm font-semibold">{title}</div>
      <div className="text-muted-foreground mt-0.5 text-xs">{detail}</div>
    </div>
  );
}

const Arrow = () => (
  <div aria-hidden className="text-muted-foreground text-center text-lg leading-none">
    ↓
  </div>
);

function Architecture() {
  return (
    <figure aria-labelledby="arch-caption" className="mx-auto grid max-w-md gap-2">
      <Box title="GitHub repos · library docs · OpenAPI specs" detail="fetched at an exact version" />
      <Arrow />
      <Box title="Ingestion" detail="split by function, class, heading or endpoint; embedded locally" />
      <Arrow />
      <Box title="Postgres + pgvector" detail="chunks, vectors and a full-text index" />
      <Arrow />
      <Box title="Hybrid search" detail="meaning (vectors) + exact words (keywords), merged" />
      <Arrow />
      <Box title="Five read-only tools" detail="search · read_file · find_symbol · get_endpoint · list_sources" accent />
      <div className="grid grid-cols-2 gap-2">
        <div className="grid gap-2">
          <Arrow />
          <Box title="Web agent" detail="Gemini calls the tools; every citation checked" />
          <Arrow />
          <Box title="This website" detail="streamed answers" />
        </div>
        <div className="grid gap-2">
          <Arrow />
          <Box title="MCP server" detail="no LLM of its own" />
          <Arrow />
          <Box title="Claude Code · Cursor" detail="your editor's model reasons" />
        </div>
      </div>
      <figcaption id="arch-caption" className="text-muted-foreground mt-2 text-center text-xs">
        The tools are shared: the web agent and the MCP server are two front doors to the same core.
      </figcaption>
    </figure>
  );
}

export default function AboutPage() {
  return (
    <div className="prose prose-sm dark:prose-invert prose-code:before:content-none prose-code:after:content-none prose-code:break-all prose-pre:overflow-x-auto mx-auto w-full max-w-3xl px-4 py-8 sm:prose-base">
      <h1>About Grepwise</h1>
      <p>
        Grepwise answers questions about code repositories, library documentation and API specs,
        and backs every claim with a citation to exact lines, like{" "}
        <code>pallets/itsdangerous@2.2.0:src/itsdangerous/timed.py:L22-L40</code>. Each citation is
        checked twice before you see it: the lines must exist at that version, and a tool must have
        actually shown them to the AI. Anything that fails is struck through.
      </p>

      <h2>How it works</h2>
      <div className="not-prose my-6">
        <Architecture />
      </div>

      <h2>Use it from Claude Code or Cursor</h2>
      <p>
        Grepwise is also an <a href="https://modelcontextprotocol.io" target="_blank" rel="noreferrer">MCP</a>{" "}
        server, so your editor&apos;s own model can search and cite the same sources. Run it locally
        from a checkout:
      </p>
      <pre>
        <code>claude mcp add grepwise -- uv run --directory /path/to/grepwise/backend grepwise mcp</code>
      </pre>
      <p>
        or connect to the hosted server&apos;s <code>/mcp</code> endpoint over HTTP. The README has
        the Cursor and Claude Desktop settings.
      </p>

      <h2>Privacy</h2>
      <ul>
        <li>
          Your questions and the answers are stored, to measure and improve answer quality. Don&apos;t
          put personal or secret information in them.
        </li>
        <li>No accounts, no cookies for tracking, and no chat history is kept on the server.</li>
        <li>
          If you add your own Gemini key, it stays in this browser tab and is sent only with your
          questions. Grepwise uses it for that request and never logs or stores it.
        </li>
        <li>Only public sources are indexed.</li>
      </ul>

      {GITHUB_URL && (
        <p>
          Source code: <a href={GITHUB_URL} target="_blank" rel="noreferrer">{GITHUB_URL.replace(/^https?:\/\//, "")}</a>
        </p>
      )}
    </div>
  );
}
