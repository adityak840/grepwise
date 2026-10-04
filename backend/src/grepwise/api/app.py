"""The FastAPI app: POST /chat (SSE) and GET /health."""

import contextlib
import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field, model_validator
from starlette.routing import Route

from grepwise.agent.llm import LLMClient, make_llm
from grepwise.agent.loop import run_agent
from grepwise.api.clientip import client_ip
from grepwise.api.ratelimit import RateLimiter
from grepwise.config import Settings, get_settings
from grepwise.ingest.embed import Embedder, LocalEmbedder, make_embedder
from grepwise.mcp.http import MCPHttpApp
from grepwise.mcp.server import build_server
from grepwise.tools import ToolContext

log = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 2_000
MAX_HISTORY_MESSAGES = 10
MAX_HISTORY_CHARS = 8_000


SITE = "*"  # the shared daily cap's key


def _too_many(code: str, message: str, wait: int) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=429,
        headers={"Retry-After": str(wait)},
    )


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
) -> FastAPI:
    """Pass `pool`, `embedder` or `llm_factory` to override the real ones (tests)."""
    settings = settings or get_settings()
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
        allow_headers=["Content-Type"],
    )
    per_ip = RateLimiter(settings.chat_rate_per_minute, settings.chat_rate_per_day)
    site = RateLimiter(None, settings.chat_global_per_day)  # shared by every visitor
    new_llm = llm_factory or (lambda: make_llm(settings.llm()))
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
        body: ChatRequest, ip: Annotated[str, Depends(ip_of)]
    ) -> StreamingResponse | JSONResponse:
        # Check both limits before counting either, so a rejected request uses up neither.
        with limits_lock:
            site_wait, ip_wait = site.wait(SITE), per_ip.wait(ip)
            if site_wait is None and ip_wait is None:
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
        llm = new_llm()

        def events() -> Iterator[str]:
            # Runs in a worker thread. If the client disconnects, the response stops pulling
            # from this generator and closes it, which stops the agent at its next event.
            with state["pool"].connection() as conn:  # type: ignore[attr-defined]
                ctx = ToolContext(conn, settings, state["embedder"])  # type: ignore[arg-type]
                agent = run_agent(
                    ctx, llm, body.question, sources=body.sources,
                    history=[m.model_dump() for m in body.history],
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

    return app
