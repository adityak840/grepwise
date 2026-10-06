"""Phase 6.1 backend additions: /api/sources, /api/read, /api/evals, X-Gemini-Key and
HOSTED_MAX_STEPS. Offline: ScriptedClient + FakeEmbedder."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from grepwise.agent.llm import LLMError, ScriptedClient, text, tool_call
from grepwise.api.app import create_app
from grepwise.config import get_settings
from grepwise.ingest.embed import FakeEmbedder

KEY = "AIzaSyTESTKEY-0123456789_abcdefghijklmn"  # shaped like a Gemini key; not real


@pytest.fixture
def pool(indexed: psycopg.Connection, empty_db_url: str) -> Iterator[ConnectionPool]:
    with ConnectionPool(
        empty_db_url, min_size=1, max_size=3, configure=register_vector,
        kwargs={"autocommit": True},
    ) as p:  # fmt: skip
        yield p


class Factories:
    """Records which LLM factory each /chat request used, and with which key."""

    def __init__(self, script: Any = None):
        self.site_calls = 0
        self.byo_keys: list[str] = []
        self.script = script or (lambda: [text("ok")])

    def site(self) -> ScriptedClient:
        self.site_calls += 1
        return ScriptedClient(self.script())

    def byo(self, key: str) -> ScriptedClient:
        self.byo_keys.append(key)
        return ScriptedClient(self.script(), provider="gemini", model="gemini-3.5-flash-lite")


def make_client(
    pool: ConnectionPool, factories: Factories | None = None, **overrides: Any
) -> TestClient:
    settings = get_settings().model_copy(update=overrides)
    f = factories or Factories()
    app = create_app(
        settings, pool=pool, embedder=FakeEmbedder(768), llm_factory=f.site,
        byo_llm_factory=f.byo,
    )  # fmt: skip
    return TestClient(app)


def sse_events(body: str) -> list[tuple[str, Any]]:
    out = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((fields["event"], json.loads(fields["data"])))
    return out


# --- /api ------------------------------------------------------------------------------------


def test_api_sources(pool: ConnectionPool) -> None:
    with make_client(pool) as client:
        response = client.get("/api/sources")
    assert response.status_code == 200
    refs = {s["ref"] for s in response.json()["sources"]}
    assert refs == {"example/mini_repo@v1", "example/docs_repo@v2", "petstore@1.0.27"}


def test_api_read(pool: ConnectionPool) -> None:
    params: dict[str, str | int] = {
        "source": "example/mini_repo@v1", "path": "web/session.ts", "start": 6, "end": 9,
    }  # fmt: skip
    with make_client(pool) as client:
        ok = client.get("/api/read", params=params)
        whole = client.get("/api/read", params={"source": "petstore", "path": "GET /pet/{petId}"})
        missing = client.get("/api/read", params={**params, "path": "nope.ts"})
        bad_range = client.get("/api/read", params={**params, "start": 99, "end": 100})
        no_path = client.get("/api/read", params={"source": "petstore"})
    assert ok.status_code == 200
    assert ok.json()["citation"] == "example/mini_repo@v1:web/session.ts:L6-L9"
    assert ok.json()["content"].startswith("export async function refreshToken")
    assert whole.json()["start_line"] == 1
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "file_not_found")
    assert (bad_range.status_code, bad_range.json()["error"]["code"]) == (400, "bad_line_range")
    assert no_path.status_code == 422


def test_api_evals(pool: ConnectionPool, tmp_path: Path) -> None:
    report = tmp_path / "latest.json"
    with make_client(pool, eval_report_path=report) as client:
        missing = client.get("/api/evals")
        report.write_text(json.dumps({"retrieval": {"recall@5": 0.9}}))
        present = client.get("/api/evals")
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "not_found")
    assert present.json() == {"retrieval": {"recall@5": 0.9}}


def test_api_reads_use_the_anonymous_per_ip_limit(pool: ConnectionPool) -> None:
    with make_client(pool, mcp_rate_per_minute=2) as client:
        assert client.get("/api/sources").status_code == 200
        assert client.get("/api/evals").status_code == 404  # counted, even when not found
        limited = client.get("/api/read", params={"source": "petstore", "path": "GET /pet/{petId}"})
    assert limited.status_code == 429 and limited.headers["retry-after"]


def test_api_cors_includes_the_ui_and_the_key_header(pool: ConnectionPool) -> None:
    preflight = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-gemini-key",
    }
    with make_client(pool, allowed_origins="http://localhost:3000") as client:
        chat = client.options("/chat", headers=preflight)
        sources = client.get("/api/sources", headers={"Origin": "http://localhost:3000"})
    assert chat.status_code == 200
    assert "x-gemini-key" in chat.headers["access-control-allow-headers"].lower()
    assert sources.headers["access-control-allow-origin"] == "http://localhost:3000"


# --- X-Gemini-Key -------------------------------------------------------------------------


def test_own_key_is_used_for_that_request(pool: ConnectionPool) -> None:
    f = Factories()
    with make_client(pool, f) as client:
        own = client.post("/chat", json={"question": "q"}, headers={"X-Gemini-Key": KEY})
        site = client.post("/chat", json={"question": "q"})
    assert own.status_code == site.status_code == 200
    assert f.byo_keys == [KEY] and f.site_calls == 1
    assert sse_events(own.text)[-1][1]["provider"] == "gemini"


def test_own_key_skips_the_site_cap_but_not_the_per_ip_limit(pool: ConnectionPool) -> None:
    f = Factories()
    with make_client(pool, f, chat_global_per_day=1, chat_rate_per_minute=2) as client:
        assert client.post("/chat", json={"question": "q"}).status_code == 200  # site cap used
        capped = client.post("/chat", json={"question": "q"})
        assert capped.json()["error"]["code"] == "daily_limit"
        own = client.post("/chat", json={"question": "q"}, headers={"X-Gemini-Key": KEY})
        assert own.status_code == 200  # their own quota: the site cap doesn't apply
        limited = client.post("/chat", json={"question": "q"}, headers={"X-Gemini-Key": KEY})
    # Rejected requests aren't counted, so this is the IP's 3rd *accepted* one: over 2/min.
    assert limited.json()["error"]["code"] == "rate_limited"


@pytest.mark.parametrize("bad", ["", "   ", "short", "has spaces in it 0123456789", "x" * 200])
def test_malformed_key_is_invalid_key(pool: ConnectionPool, bad: str) -> None:
    f = Factories()
    with make_client(pool, f) as client:
        response = client.post("/chat", json={"question": "q"}, headers={"X-Gemini-Key": bad})
    assert (response.status_code, response.json()["error"]["code"]) == (400, "invalid_key")
    assert f.byo_keys == [] and f.site_calls == 0


def test_rejected_key_streams_invalid_key(pool: ConnectionPool) -> None:
    f = Factories(script=lambda: [LLMError("invalid_key", "The gemini API key was rejected.")])
    with make_client(pool, f) as client:
        response = client.post("/chat", json={"question": "q"}, headers={"X-Gemini-Key": KEY})
    assert sse_events(response.text)[-1] == (
        "error", {"code": "invalid_key", "message": "The gemini API key was rejected."},
    )  # fmt: skip


def test_key_never_appears_in_logs_responses_or_agent_runs(
    pool: ConnectionPool, indexed: psycopg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    read = json.dumps({"source": "example/mini_repo@v1", "path": "README.md"})
    f = Factories(script=lambda: [tool_call("read_file", read), text("README says hi.")])
    with make_client(pool, f) as client:
        response = client.post("/chat", json={"question": "q"}, headers={"X-Gemini-Key": KEY})
    assert KEY not in response.text
    assert KEY not in caplog.text
    rows = indexed.execute("SELECT row_to_json(r)::text, byo_key FROM agent_runs r").fetchall()
    assert rows and all(KEY not in row[0] for row in rows)
    assert [row[1] for row in rows] == [True]


def test_site_key_runs_are_not_marked_byo(
    pool: ConnectionPool, indexed: psycopg.Connection
) -> None:
    with make_client(pool) as client:
        client.post("/chat", json={"question": "q"})
    assert indexed.execute("SELECT byo_key FROM agent_runs").fetchall() == [(False,)]


# --- HOSTED_MAX_STEPS ---------------------------------------------------------------------


def test_hosted_max_steps_limits_chat(pool: ConnectionPool) -> None:
    calls = [json.dumps({"query": f"q{i}"}) for i in range(10)]
    f = Factories(script=lambda: [tool_call("search", c) for c in calls] + [text("best answer")])
    with make_client(pool, f, hosted_max_steps=2, agent_max_steps=8) as client:
        events = sse_events(client.post("/chat", json={"question": "q"}).text)
    steps = [data for kind, data in events if kind == "step"]
    assert len(steps) == 2  # not AGENT_MAX_STEPS (8): that one is for the CLI
    assert events[-1][0] == "done" and events[-1][1]["steps"] == 2
