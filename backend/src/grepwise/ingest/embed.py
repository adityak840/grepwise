"""Turning text into unit-length embedding vectors."""

import hashlib
import logging
import re
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import numpy as np
import numpy.typing as npt
from google import genai
from google.genai import errors, types

from grepwise.config import Settings

log = logging.getLogger(__name__)

Vector = npt.NDArray[np.float32]

MAX_TRIES = 5
_RETRYABLE_CODES = {429, 500, 503}  # rate limited, or a transient server error
_MAX_RETRY_DELAY = 120.0

# Gemini embeds stored text and search questions slightly differently.
DOCUMENT = "RETRIEVAL_DOCUMENT"
QUERY = "RETRIEVAL_QUERY"


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


# Some models expect a task prefix on every input (the local equivalent of Gemini's task type).
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
    def model(self) -> LocalModel:
        if self._model is None:
            self._model = self._factory()
        return self._model

    def _embed_batch(self, texts: list[str], task: str = DOCUMENT) -> list[list[float]]:
        prefix = self._prefixes.get(task, "")
        out = self.model.embed([prefix + t for t in texts], batch_size=len(texts))
        return [np.asarray(v, dtype=np.float32).tolist() for v in out]


@dataclass(frozen=True)
class QuotaInfo:
    """What a Gemini 429 says about the limit that was hit."""

    retry_after: float | None  # seconds, from google.rpc.RetryInfo
    quota_id: str | None  # e.g. 'EmbedContentRequestsPerDayPerProjectPerModel-FreeTier'

    @property
    def is_daily(self) -> bool:
        return self.quota_id is not None and "PerDay" in self.quota_id


def quota_info(exc: errors.APIError) -> QuotaInfo:
    body: Any = exc.details
    details = body.get("error", {}).get("details", []) if isinstance(body, dict) else []
    retry_after, quota_id = None, None
    for d in details if isinstance(details, list) else []:
        kind = str(d.get("@type", ""))
        if kind.endswith("RetryInfo"):
            match = re.fullmatch(r"([\d.]+)s", str(d.get("retryDelay", "")))
            retry_after = float(match[1]) if match else None
        elif kind.endswith("QuotaFailure"):
            violations = d.get("violations") or [{}]
            quota_id = violations[0].get("quotaId")
    return QuotaInfo(retry_after, quota_id)


class GeminiEmbedder(Embedder):
    # Small requests spread the work across the per-minute token quota.
    max_batch_tokens = 8_000

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-embedding-001",
        dim: int = 768,
        *,
        tokens_per_minute: int = 25_000,
        client: genai.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        base_delay: float = 4.0,
    ):
        super().__init__(dim)
        if client is None and not api_key:
            raise EmbeddingError("GEMINI_API_KEY is not set (add it to .env)")
        self.client = client or genai.Client(api_key=api_key)
        self.model = model
        self.tokens_per_minute = tokens_per_minute
        self.sleep = sleep
        self.clock = clock
        self.base_delay = base_delay
        self._sent: deque[tuple[float, int]] = deque()  # (time, estimated tokens)

    @property
    def model_id(self) -> str:
        return f"gemini:{self.model}:{self.dim}"

    def _wait_for_budget(self, tokens: int) -> None:
        """Sleep until sending `tokens` keeps the last 60 s under `tokens_per_minute`."""
        while True:
            now = self.clock()
            while self._sent and now - self._sent[0][0] >= 60:
                self._sent.popleft()
            used = sum(n for _, n in self._sent)
            if not self._sent or used + tokens <= self.tokens_per_minute:
                break
            wait = 60 - (now - self._sent[0][0])
            log.info("Pacing: waiting %.0fs (limit %s tokens/min)", wait, self.tokens_per_minute)
            self.sleep(wait)
        self._sent.append((self.clock(), tokens))

    def _embed_batch(self, texts: list[str], task: str = DOCUMENT) -> list[list[float]]:
        config = types.EmbedContentConfig(task_type=task, output_dimensionality=self.dim)
        tokens = sum(estimate_tokens(t) for t in texts)
        # Plain strings are valid contents; the SDK's list type is invariant, so tell mypy.
        contents = cast(types.ContentListUnion, texts)
        for attempt in range(1, MAX_TRIES + 1):
            self._wait_for_budget(tokens)
            try:
                response = self.client.models.embed_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                return [list(e.values or []) for e in response.embeddings or []]
            except errors.APIError as exc:
                quota = quota_info(exc)
                message = f"Gemini embedding failed: {exc.code} {exc.status}"
                if quota.quota_id:
                    message += f" (quota: {quota.quota_id})"
                if quota.is_daily:
                    raise EmbeddingError(f"{message}. Daily quota used up; try tomorrow.") from exc
                if exc.code not in _RETRYABLE_CODES or attempt == MAX_TRIES:
                    raise EmbeddingError(message) from exc
                # Gemini's own retryDelay if it sent one, else 4, 8, 16, 32 s.
                delay = quota.retry_after or self.base_delay * 2 ** (attempt - 1)
                delay = min(delay, _MAX_RETRY_DELAY)
                log.warning("Gemini returned %s; retrying in %.0fs", exc.code, delay)
                self.sleep(delay)
        raise AssertionError("unreachable")


def make_embedder(settings: Settings) -> Embedder:
    if settings.embedder == "fake":
        return FakeEmbedder(settings.embedding_dim)
    if settings.embedder == "local":
        return LocalEmbedder(
            settings.local_embedding_model,
            settings.embedding_dim,
            cache_dir=settings.model_cache_dir,
        )
    return GeminiEmbedder(
        settings.gemini_api_key.get_secret_value(),
        settings.embedding_model,
        settings.embedding_dim,
        tokens_per_minute=settings.embed_tokens_per_minute,
    )
