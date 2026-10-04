"""MCP over Streamable HTTP at /mcp, mounted in the FastAPI app.

Stateless (no server-side sessions, which suits one small instance) and read-only. Access:
anonymous requests are rate limited per client IP; `Authorization: Bearer <key>` with a key
from MCP_API_KEYS gets a higher limit and no daily cap. A wrong key is a 401, never a silent
fallback to anonymous. Keys are compared in constant time and never logged.
"""

import hashlib
import hmac
import json
import logging
from typing import Any

from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import HTTPConnection
from starlette.types import Receive, Scope, Send

from grepwise.api.clientip import client_ip
from grepwise.api.ratelimit import RateLimiter
from grepwise.config import Settings

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024


async def _send_json(
    send: Send, status: int, body: dict[str, Any], headers: dict[str, str] | None = None
) -> None:
    payload = json.dumps(body).encode()
    raw = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    raw += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": payload})


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


class MCPHttpApp:
    """An ASGI app: access checks, then the SDK's session manager."""

    def __init__(self, server: Server[Any], settings: Settings):
        self.settings = settings
        hosts = [h.strip() for h in settings.mcp_allowed_hosts.split(",") if h.strip()]
        self.manager = StreamableHTTPSessionManager(
            app=server,
            stateless=True,
            json_response=True,
            max_request_body_size=MAX_BODY_BYTES,
            # DNS-rebinding protection: only these Host headers, and no browser origins
            # (MCP clients aren't browsers, so /mcp needs no CORS).
            security_settings=TransportSecuritySettings(
                enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=[]
            ),
        )
        keys = settings.mcp_api_keys.get_secret_value()
        self._keys = [k.strip().encode() for k in keys.split(",") if k.strip()]
        self.anonymous = RateLimiter(settings.mcp_rate_per_minute, settings.mcp_rate_per_day)
        self.keyed = RateLimiter(settings.mcp_key_rate_per_minute, None)

    def _valid_key(self, key: str) -> bool:
        candidate = key.encode()
        # Compare against every key (no early exit), each in constant time.
        return any([hmac.compare_digest(candidate, k) for k in self._keys])

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.manager.handle_request(scope, receive, send)
            return
        conn = HTTPConnection(scope)
        length = conn.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            await _send_json(send, 413, _error("too_large", "Request body is over 64 KB."))
            return

        auth = conn.headers.get("authorization")
        if auth is not None:
            scheme, _, key = auth.partition(" ")
            if scheme.lower() != "bearer" or not key.strip() or not self._valid_key(key.strip()):
                await _send_json(
                    send, 401, _error("unauthorized", "Invalid API key."),
                    {"WWW-Authenticate": 'Bearer realm="grepwise"'},
                )  # fmt: skip
                return
            limiter = self.keyed
            # Identify the key by a hash prefix, so the key itself is never kept or logged.
            who = "key:" + hashlib.sha256(key.strip().encode()).hexdigest()[:16]
        else:
            limiter = self.anonymous
            who = client_ip(conn, self.settings.trust_proxy_headers)

        wait = limiter.check(who)
        if wait is not None:
            await _send_json(
                send, 429, _error("rate_limited", "Too many MCP requests; slow down."),
                {"Retry-After": str(wait)},
            )  # fmt: skip
            return
        await self.manager.handle_request(scope, receive, send)
