"""The FastAPI app: POST /chat (SSE), GET /health, the read-only /api/* used by the web UI,
and MCP at /mcp."""

import contextlib
import dataclasses
import json
import logging
import re
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field, SecretStr, model_validator
from starlette.routing import Route

from grepwise.agent.llm import LLMClient, make_llm
from grepwise.agent.loop import run_agent
from grepwise.api.clientip import client_ip
from grepwise.api.ratelimit import RateLimiter
from grepwise.config import Settings, get_settings
from grepwise.ingest.embed import Embedder, LocalEmbedder, make_embedder
from grepwise.mcp.http import MCPHttpApp
from grepwise.mcp.server import build_server
from grepwise.tools import ToolContext, call_tool, is_error

log = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 2_000
MAX_HISTORY_MESSAGES = 10
MAX_HISTORY_CHARS = 8_000


SITE = "*"  # the shared daily cap's key
# Plausible API keys only (Gemini keys are ~39 URL-safe characters); anything else is rejected
# before it gets near a provider.
_KEY_SHAPE = re.compile(r"^[A-Za-z0-9_\-]{20,128}$")
# Tool error code -> HTTP status for the /api endpoints.
_ERROR_STATUS = {"source_not_found": 404, "file_not_found": 404, "endpoint_not_found": 404,
                 "not_reindexed": 409, "internal": 500}  # fmt: skip


def _error(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status, headers=headers)


def _too_many(code: str, message: str, wait: int) -> JSONResponse:
    return _error(429, code, message, {"Retry-After": str(wait)})


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    sources: list[str] | None = Field(default=None, max_length=20)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)

    @model_validator(mode="after")
    def _history_size(self) -> "ChatRequest":
        if sum(len(m.content) for m in self.history) > MAX_HISTORY_CHARS:
            raise ValueError(f"history is longer than {MAX_HISTORY_CHARS} characters in total")
        return self


def _configure(conn: psycopg.Connection) -> None:
    register_vector(conn)


def create_app(
    settings: Settings | None = None,
    *,
    pool: ConnectionPool | None = None,
    embedder: Embedder | None = None,
    llm_factory: Callable[[], LLMClient] | None = None,
    byo_llm_factory: Callable[[str], LLMClient] | None = None,
) -> FastAPI:
    """Pass `pool`, `embedder`, `llm_factory` or `byo_llm_factory` (the client for a visitor's
    own Gemini key) to override the real ones (tests)."""
    settings = settings or get_settings()
    # Provider SDKs can log request details at DEBUG; never let them near a visitor's key.
    for name in ("openai", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    state: dict[str, object] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        own_pool = pool is None
        state["pool"] = pool or ConnectionPool(
            settings.database_url, min_size=1, max_size=10, configure=_configure,
            kwargs={"autocommit": True}, open=True,
        )  # fmt: skip
        emb = embedder or make_embedder(settings)
        if isinstance(emb, LocalEmbedder):
            emb.embed_query("warm up")  # load the model once, at startup, not on a request
        state["embedder"] = emb
        try:
            async with mcp_http.manager.run():  # the MCP session manager lives with the app
                yield
        finally:
            if own_pool:
                state["pool"].close()  # type: ignore[attr-defined]

    @contextlib.contextmanager
    def mcp_context() -> Iterator[ToolContext]:
        """Each MCP request: a pooled connection and the shared embedder."""
        with state["pool"].connection() as conn:  # type: ignore[attr-defined]
            yield ToolContext(conn, settings, state["embedder"])  # type: ignore[arg-type]

    mcp_http = MCPHttpApp(build_server(mcp_context), settings)

    app = FastAPI(title="Grepwise", lifespan=lifespan)
    # A plain route (not a Mount), so POST /mcp isn't redirected to /mcp/.
    app.router.routes.append(Route("/mcp", mcp_http))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Gemini-Key"],
    )
    per_ip = RateLimiter(settings.chat_rate_per_minute, settings.chat_rate_per_day)
    site = RateLimiter(None, settings.chat_global_per_day)  # shared by every visitor
    reads = RateLimiter(settings.mcp_rate_per_minute, settings.mcp_rate_per_day)  # /api/*
    new_llm = llm_factory or (lambda: make_llm(settings.llm()))

    def default_byo_llm(key: str) -> LLMClient:
        """A visitor's own key: Gemini, for this one request only."""
        config = dataclasses.replace(settings.llm("gemini"), api_key=SecretStr(key))
        return make_llm(config)

    new_byo_llm = byo_llm_factory or default_byo_llm
    limits_lock = threading.Lock()

    def ip_of(request: Request) -> str:
        return client_ip(request, settings.trust_proxy_headers)

    @app.get("/health")
    def health() -> dict[str, object]:
        db_ok = False
        try:
            with state["pool"].connection(timeout=5) as conn:  # type: ignore[attr-defined]
                db_ok = conn.execute("SELECT 1").fetchone() == (1,)
        except Exception:
            log.warning("Health check: database unreachable", exc_info=True)
        emb = state.get("embedder")
        loaded = emb is not None and (not isinstance(emb, LocalEmbedder) or emb.loaded)
        return {"ok": db_ok, "db": db_ok, "embedder_loaded": loaded,
                "provider": settings.llm_provider}  # fmt: skip

    @app.post("/chat", response_model=None)
    def chat(
        body: ChatRequest,
        ip: Annotated[str, Depends(ip_of)],
        gemini_key: Annotated[str | None, Header(alias="X-Gemini-Key")] = None,
    ) -> StreamingResponse | JSONResponse:
        # The key is used for this request's LLM client only: never logged or stored.
        key = gemini_key.strip() if gemini_key else None
        if gemini_key is not None and not (key and _KEY_SHAPE.match(key)):
            return _error(400, "invalid_key", "That doesn't look like a Gemini API key.")
        # Check every limit before counting any, so a rejected request uses up none. A
        # visitor's own key skips the site-wide cap (it spends their quota, not ours).
        with limits_lock:
            site_wait = None if key else site.wait(SITE)
            ip_wait = per_ip.wait(ip)
            if site_wait is None and ip_wait is None:
                if not key:
                    site.hit(SITE)
                per_ip.hit(ip)
        if site_wait is not None:
            return _too_many(
                "daily_limit",
                "Grepwise has answered all the questions it can for today. "
                "Please come back tomorrow.",
                site_wait,
            )
        if ip_wait is not None:
            return _too_many(
                "rate_limited", "You're asking questions too quickly; please wait a bit.", ip_wait
            )
        byo = key is not None
        llm = new_byo_llm(key) if key else new_llm()
        del key, gemini_key  # nothing below needs the key itself

        def events() -> Iterator[str]:
            # Runs in a worker thread. If the client disconnects, the response stops pulling
            # from this generator and closes it, which stops the agent at its next event.
            with state["pool"].connection() as conn:  # type: ignore[attr-defined]
                ctx = ToolContext(conn, settings, state["embedder"])  # type: ignore[arg-type]
                agent = run_agent(
                    ctx, llm, body.question, sources=body.sources,
                    history=[m.model_dump() for m in body.history],
                    max_steps=settings.hosted_max_steps, byo_key=byo,
                )  # fmt: skip
                try:
                    for event in agent:
                        yield event.to_sse()
                finally:
                    agent.close()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # --- Read-only endpoints for the web UI -----------------------------------------------

    def read_limit(ip: str) -> JSONResponse | None:
        wait = reads.check(ip)
        return _too_many("rate_limited", "Too many requests; slow down.", wait) if wait else None

    def run_read_tool(name: str, args: dict[str, object]) -> JSONResponse:
        with state["pool"].connection() as conn:  # type: ignore[attr-defined]
            ctx = ToolContext(conn, settings, state["embedder"])  # type: ignore[arg-type]
            result = call_tool(ctx, name, args)
        if is_error(result):
            status = _ERROR_STATUS.get(result["error"]["code"], 400)
            return JSONResponse(result, status)
        return JSONResponse(result)

    @app.get("/api/sources", response_model=None)
    def api_sources(ip: Annotated[str, Depends(ip_of)]) -> JSONResponse:
        """The list_sources tool's output."""
        return read_limit(ip) or run_read_tool("list_sources", {})

    @app.get("/api/read", response_model=None)
    def api_read(
        ip: Annotated[str, Depends(ip_of)],
        source: str,
        path: str,
        start: int | None = None,
        end: int | None = None,
    ) -> JSONResponse:
        """The read_file tool's output (for the citation viewer's "show more context")."""
        args: dict[str, object] = {"source": source, "path": path}
        if start is not None:
            args["start_line"] = start
        if end is not None:
            args["end_line"] = end
        return read_limit(ip) or run_read_tool("read_file", args)

    @app.get("/api/evals", response_model=None)
    def api_evals(ip: Annotated[str, Depends(ip_of)]) -> JSONResponse:
        """The latest eval report (phase 6.2), or 404 before there is one."""
        if limited := read_limit(ip):
            return limited
        path = settings.eval_report_path
        if not path.is_file():
            return _error(404, "not_found", "No eval report yet. Run `grepwise eval report`.")
        return JSONResponse(json.loads(path.read_text()))

    return app
