from typing import Any

import numpy as np
import pytest
from google.genai import errors

from grepwise.ingest.embed import EmbeddingError, FakeEmbedder, GeminiEmbedder, normalize


def test_normalize_gives_unit_length() -> None:
    assert np.linalg.norm(normalize([3.0, 4.0])) == pytest.approx(1.0, abs=1e-6)
    with pytest.raises(EmbeddingError):
        normalize([0.0, 0.0])


def test_fake_embedder_is_deterministic_and_normalized() -> None:
    embedder = FakeEmbedder(dim=768)
    a, b, a2 = embedder.embed(["alpha", "beta", "alpha"])
    assert a.shape == (768,)
    assert np.allclose(a, a2) and not np.allclose(a, b)
    for v in (a, b):
        assert np.linalg.norm(v) == pytest.approx(1.0, abs=1e-3)


def test_fake_embedder_batches() -> None:
    embedder = FakeEmbedder(dim=8)
    embedder.batch_size = 2
    assert len(embedder.embed([str(i) for i in range(5)])) == 5
    assert embedder.calls == 3


class _Embedding:
    def __init__(self, values: list[float]):
        self.values = values


class _Response:
    def __init__(self, n: int, dim: int):
        self.embeddings = [_Embedding([1.0] * dim) for _ in range(n)]


def api_error(code: int, details: list[dict[str, Any]] | None = None) -> errors.APIError:
    body = {"error": {"status": "X", "message": "m", "details": details or []}}
    return errors.APIError(code, body)


class _StubModels:
    """Stands in for `client.models`; raises `failures` first, then succeeds."""

    def __init__(self, failures: list[int | errors.APIError], dim: int):
        self.failures = list(failures)
        self.dim = dim
        self.calls: list[dict[str, Any]] = []

    def embed_content(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        if self.failures:
            f = self.failures.pop(0)
            raise f if isinstance(f, errors.APIError) else api_error(f)
        return _Response(len(kwargs["contents"]), self.dim)


class _StubClient:
    def __init__(self, models: _StubModels):
        self.models = models


class _Clock:
    """Fake time: `sleep` advances it, so pacing tests run instantly."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_gemini(
    failures: list[int | errors.APIError], dim: int = 4, tokens_per_minute: int = 25_000
) -> tuple[GeminiEmbedder, _StubModels, list[float]]:
    models = _StubModels(failures, dim)
    clock = _Clock()
    embedder = GeminiEmbedder(
        "",
        dim=dim,
        tokens_per_minute=tokens_per_minute,
        client=_StubClient(models),  # type: ignore[arg-type]
        sleep=clock.sleep,
        clock=clock,
    )
    return embedder, models, clock.sleeps


def test_gemini_retries_rate_limits_with_backoff() -> None:
    embedder, models, sleeps = make_gemini([429, 429])
    vectors = embedder.embed(["a", "b"])
    assert len(vectors) == 2
    assert np.linalg.norm(vectors[0]) == pytest.approx(1.0, abs=1e-6)  # [1,1,1,1] normalised
    assert sleeps == [4.0, 8.0]
    config = models.calls[0]["config"]
    assert config.task_type == "RETRIEVAL_DOCUMENT" and config.output_dimensionality == 4


def test_gemini_gives_up_after_five_tries() -> None:
    embedder, models, _ = make_gemini([429] * 5)
    with pytest.raises(EmbeddingError, match="429"):
        embedder.embed(["a"])
    assert len(models.calls) == 5


def test_gemini_does_not_retry_client_errors() -> None:
    embedder, models, _ = make_gemini([400])
    with pytest.raises(EmbeddingError):
        embedder.embed(["a"])
    assert len(models.calls) == 1


def test_gemini_requires_a_key() -> None:
    with pytest.raises(EmbeddingError, match="GEMINI_API_KEY"):
        GeminiEmbedder("")


def test_gemini_uses_the_retry_delay_gemini_sends() -> None:
    retry_info = {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"}
    embedder, _, sleeps = make_gemini([api_error(429, [retry_info])])
    embedder.embed(["a"])
    assert sleeps == [37.0]


def test_gemini_fails_fast_when_the_daily_quota_is_used_up() -> None:
    failure = {
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{"quotaId": "EmbedContentRequestsPerDayPerProjectPerModel-FreeTier"}],
    }
    embedder, models, _ = make_gemini([api_error(429, [failure])])
    with pytest.raises(EmbeddingError, match=r"PerDay.*Daily quota used up"):
        embedder.embed(["a"])
    assert len(models.calls) == 1


def test_gemini_batches_by_estimated_tokens() -> None:
    embedder, models, _ = make_gemini([], tokens_per_minute=10**9)
    texts = ["x" * 4_000] * 20  # ~1,000 tokens each, so 7 fit under the 8,000-token cap
    assert len(embedder.embed(texts)) == 20
    assert [len(c["contents"]) for c in models.calls] == [7, 7, 6]


def test_gemini_paces_requests_under_the_per_minute_budget() -> None:
    embedder, models, sleeps = make_gemini([], tokens_per_minute=2_500)
    embedder.embed(["x" * 4_000] * 20)  # 3 batches of ~7,000 tokens, each over the budget
    assert len(models.calls) == 3
    assert sleeps == [60.0, 60.0]  # each later batch waits for the window to clear


def test_model_id_names_the_embedder_and_dimension() -> None:
    assert FakeEmbedder(dim=8).model_id == "fake:8"
    embedder, _, _ = make_gemini([], dim=4)
    assert embedder.model_id == "gemini:gemini-embedding-001:4"
