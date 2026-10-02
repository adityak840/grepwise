"""Settings, read from environment variables and the repo-root `.env` file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/src/grepwise/config.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Later files win; real environment variables beat both.
        env_file=(REPO_ROOT / ".env", ".env"),
        extra="ignore",
    )

    database_url: str = "postgresql://grepwise:grepwise@localhost:5432/grepwise"
    # SecretStr keeps the key out of reprs, logs and tracebacks.
    gemini_api_key: SecretStr = SecretStr("")
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 768
    embedder: Literal["gemini", "fake"] = "gemini"
    # Stay under Gemini's per-minute token quota (free tier is tight). Raise on a paid key.
    embed_tokens_per_minute: int = 20_000
    # Hybrid search: candidates taken from each of vector and keyword search, and the
    # (estimated) token budget for the returned results.
    search_candidates: int = 50
    search_token_budget: int = 4_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
