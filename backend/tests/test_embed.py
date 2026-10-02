from typing import Any

import numpy as np
import pytest
from google.genai import errors

from grepwise.config import Settings
from grepwise.ingest.embed import (
    EmbeddingError,
    FakeEmbedder,
    GeminiEmbedder,
    LocalEmbedder,
    make_embedder,
    normalize,
)


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


class _StubLocalModel:
    """Stands in for fastembed.TextEmbedding: records inputs, returns fixed vectors."""

    def __init__(self, dim: int) -> None:
        self.dim = dim
        self.inputs: list[list[str]] = []

    def embed(self, texts: list[str], batch_size: int = 256) -> list[np.ndarray]:
        self.inputs.append(list(texts))
        return [np.arange(1, self.dim + 1, dtype=np.float32) for _ in texts]


def make_local(dim: int = 4) -> tuple[LocalEmbedder, list[_StubLocalModel]]:
    loaded: list[_StubLocalModel] = []

    def factory() -> _StubLocalModel:
        loaded.append(_StubLocalModel(dim))
        return loaded[-1]

    return LocalEmbedder("nomic-ai/nomic-embed-text-v1.5-Q", dim, model_factory=factory), loaded


def test_local_embedder_adds_nomic_task_prefixes() -> None:
    embedder, loaded = make_local()
    vectors = embedder.embed(["chunk one", "chunk two"])
    query = embedder.embed_query("how do I sign?")
    assert loaded[0].inputs == [
        ["search_document: chunk one", "search_document: chunk two"],
        ["search_query: how do I sign?"],
    ]
    assert len(vectors) == 2
    assert np.linalg.norm(query) == pytest.approx(1.0, abs=1e-6)


def test_local_embedder_loads_the_model_once_and_lazily() -> None:
    embedder, loaded = make_local()
    assert loaded == []  # nothing loaded until the first embed
    embedder.embed(["a"])
    embedder.embed_query("b")
    assert len(loaded) == 1


def test_local_embedder_sends_small_batches() -> None:
    embedder, loaded = make_local()
    embedder.embed([f"t{i}" for i in range(40)])
    assert [len(b) for b in loaded[0].inputs] == [16, 16, 8]


def test_local_model_id_and_default_embedder() -> None:
    embedder, _ = make_local(dim=768)
    assert embedder.model_id == "local:nomic-ai/nomic-embed-text-v1.5-Q:768"
    default = make_embedder(Settings(_env_file=None))  # type: ignore[call-arg]
    assert isinstance(default, LocalEmbedder)  # created without loading the model


class _LengthStubModel(_StubLocalModel):
    """Returns a vector that encodes each input's length, so order can be checked."""

    def embed(self, texts: list[str], batch_size: int = 256) -> list[np.ndarray]:
        self.inputs.append(list(texts))
        return [np.array([len(t), 1, 1, 1], dtype=np.float32) for t in texts]


def test_local_embedder_groups_by_length_and_keeps_order() -> None:
    model = _LengthStubModel(4)
    embedder = LocalEmbedder("other/model", 4, model_factory=lambda: model)
    long_text, short = "x" * 6000, "short"
    texts = [long_text, short, long_text, short, short]
    vectors = embedder.embed(texts)
    # Long texts go alone (cap ~1,000 estimated tokens); short ones share a group.
    assert sorted(len(b) for b in model.inputs) == [1, 1, 3]
    assert all(len({len(t) for t in batch}) == 1 for batch in model.inputs)  # no mixing
    # Results come back in the caller's order.
    lengths = [round(float(v[0] / v[1])) for v in vectors]
    assert lengths == [len(t) for t in texts]
