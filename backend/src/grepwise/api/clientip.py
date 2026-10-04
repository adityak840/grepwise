"""Which client a request came from, for per-client rate limits (/chat and /mcp)."""

from starlette.requests import HTTPConnection


def client_ip(conn: HTTPConnection, trust_proxy_headers: bool) -> str:
    forwarded = conn.headers.get("x-forwarded-for")
    if trust_proxy_headers and forwarded:
        # The proxy appends the address it saw; entries before it came from the client and
        # can be anything. So the right-most entry is the only trustworthy one.
        entries = [e.strip() for e in forwarded.split(",") if e.strip()]
        if entries:
            return entries[-1]
    return conn.client.host if conn.client else "unknown"
