import numpy as np
import pytest

from grepwise.config import Settings
from grepwise.ingest.embed import (
    EmbeddingError,
    FakeEmbedder,
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


def test_model_id_names_the_embedder_and_dimension() -> None:
    assert FakeEmbedder(dim=8).model_id == "fake:8"


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
    assert isinstance(make_embedder(Settings(_env_file=None, embedder="fake")), FakeEmbedder)  # type: ignore[call-arg]


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
