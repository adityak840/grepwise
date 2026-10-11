"""Turning text into unit-length embedding vectors."""

import hashlib
import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from grepwise.config import Settings

log = logging.getLogger(__name__)

Vector = npt.NDArray[np.float32]

# Some models embed stored text and search questions slightly differently (see _PREFIXES).
DOCUMENT = "document"
QUERY = "query"


class EmbeddingError(Exception):
    pass


def estimate_tokens(text: str) -> int:
    return len(text) // 4 + 1


def normalize(vector: Sequence[float] | Vector) -> Vector:
    """Scale to length 1, so cosine similarity is a plain dot product."""
    arr = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm == 0 or not np.isfinite(norm):
        raise EmbeddingError("cannot normalise a zero or non-finite vector")
    return arr / norm


class Embedder(ABC):
    batch_size = 100
    max_batch_tokens: int | None = None  # also cap each request by estimated tokens
    # Group similar-length texts together (results still come back in the caller's order).
    # Local models pad every text in a group to the longest one, so mixing lengths wastes
    # memory and time.
    sort_by_length = False

    def __init__(self, dim: int):
        self.dim = dim

    @property
    @abstractmethod
    def model_id(self) -> str:
        """Names the model and dimension; stored with each vector so reuse never mixes models."""

    @abstractmethod
    def _embed_batch(self, texts: list[str], task: str = DOCUMENT) -> list[list[float]]: ...

    def _batches(self, texts: Sequence[str]) -> Iterator[list[str]]:
        batch: list[str] = []
        tokens = 0
        for text in texts:
            n = estimate_tokens(text)
            too_many = len(batch) >= self.batch_size
            too_big = self.max_batch_tokens is not None and tokens + n > self.max_batch_tokens
            if batch and (too_many or too_big):
                yield batch
                batch, tokens = [], 0
            batch.append(text)
            tokens += n
        if batch:
            yield batch

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        """Embed chunks in batches; every returned vector has length `dim` and norm 1.
        Vectors are returned in the same order as `texts`."""
        order = list(range(len(texts)))
        if self.sort_by_length:
            order.sort(key=lambda i: len(texts[i]))
        embedded: list[Vector] = []
        for batch in self._batches([texts[i] for i in order]):
            embedded.extend(self._checked(batch, self._embed_batch(batch, DOCUMENT)))
        out: list[Vector] = [embedded[0]] * len(texts) if embedded else []
        for position, index in enumerate(order):
            out[index] = embedded[position]
        return out

    def embed_query(self, query: str) -> Vector:
        """Embed a search question (same model and dimension as the chunks, normalised)."""
        return self._checked([query], self._embed_batch([query], QUERY))[0]

    def _checked(self, batch: list[str], raw: list[list[float]]) -> list[Vector]:
        if len(raw) != len(batch):
            raise EmbeddingError(f"asked for {len(batch)} embeddings, got {len(raw)}")
        for values in raw:
            if len(values) != self.dim:
                raise EmbeddingError(f"expected {self.dim} dims, got {len(values)}")
        return [normalize(values) for values in raw]


class FakeEmbedder(Embedder):
    """Deterministic pseudo-random vectors seeded from the text's hash. No network."""

    def __init__(self, dim: int = 768):
        super().__init__(dim)
        self.calls = 0
        self.texts_embedded = 0

    @property
    def model_id(self) -> str:
        return f"fake:{self.dim}"

    def _embed_batch(self, texts: list[str], task: str = DOCUMENT) -> list[list[float]]:
        self.calls += 1
        self.texts_embedded += len(texts)
        out = []
        for text in texts:
            seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
            out.append(np.random.default_rng(seed).standard_normal(self.dim).tolist())
        return out


class LocalModel(Protocol):
    """The part of fastembed.TextEmbedding we use (so tests can pass a stub)."""

    def embed(self, documents: list[str], batch_size: int = ...) -> Iterable[Any]: ...


# Some models expect a task prefix on every input.
_PREFIXES = {
    "nomic-ai/": {DOCUMENT: "search_document: ", QUERY: "search_query: "},
}


class LocalEmbedder(Embedder):
    """An open-source embedding model run on this machine via fastembed (ONNX, CPU).

    No quota or network after the one-time model download. The model loads on first use.
    """

    # Memory grows with the square of input length, and a group is padded to its longest text.
    # Measured on 12 long + 24 short chunks: a ~1,000-token cap per group peaked at 1.9 GB in
    # 18 s; a 2,000-token cap took 8.4 GB and 64 s. So long chunks go alone, short ones in
    # groups of up to 16, sorted by length. Queries are short and cheap.
    batch_size = 16
    max_batch_tokens = 1_000
    sort_by_length = True

    def __init__(
        self,
        model: str = "nomic-ai/nomic-embed-text-v1.5-Q",
        dim: int = 768,
        *,
        cache_dir: Path | None = None,
        threads: int | None = None,
        model_factory: Callable[[], LocalModel] | None = None,
    ):
        super().__init__(dim)
        self.model_name = model
        self.cache_dir = cache_dir
        self.threads = threads
        self._factory = model_factory or self._load_fastembed
        self._model: LocalModel | None = None
        self._load_lock = threading.Lock()
        self._prefixes = next(
            (p for key, p in _PREFIXES.items() if model.startswith(key)), {DOCUMENT: "", QUERY: ""}
        )

    @property
    def model_id(self) -> str:
        return f"local:{self.model_name}:{self.dim}"

    def _load_fastembed(self) -> LocalModel:
        from fastembed import TextEmbedding  # heavy import: only when actually embedding

        log.info("Loading local embedding model %s", self.model_name)
        cache = str(self.cache_dir) if self.cache_dir else None
        model: LocalModel = TextEmbedding(self.model_name, cache_dir=cache, threads=self.threads)
        return model

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def model(self) -> LocalModel:
        if self._model is None:
            with self._load_lock:  # a warm-up and a first query may race to load it
                if self._model is None:
                    self._model = self._factory()
        return self._model

    def _embed_batch(self, texts: list[str], task: str = DOCUMENT) -> list[list[float]]:
        prefix = self._prefixes.get(task, "")
        out = self.model.embed([prefix + t for t in texts], batch_size=len(texts))
        return [np.asarray(v, dtype=np.float32).tolist() for v in out]


def make_embedder(settings: Settings) -> Embedder:
    if settings.embedder == "fake":
        return FakeEmbedder(settings.embedding_dim)
    return LocalEmbedder(
        settings.local_embedding_model,
        settings.embedding_dim,
        cache_dir=settings.model_cache_dir,
    )
