"""Settings, from GREPWISE_* environment variables (and this checkout's `.env`, for development).

Every name has the GREPWISE_ prefix: the MCP server runs inside other people's projects, and a
generic name like EMBEDDER could collide with their own environment.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/src/grepwise/config.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GREPWISE_",
        env_file=REPO_ROOT / ".env",  # real environment variables win
        extra="ignore",
    )

    # Everything Grepwise stores: the database and the embedding model.
    home: Path = Path.home() / ".grepwise"
    # local: an open-source model run on this machine. fake: deterministic vectors for tests.
    embedder: Literal["local", "fake"] = "local"
    local_embedding_model: str = "nomic-ai/nomic-embed-text-v1.5-Q"
    embedding_dim: int = 768
    # Hybrid search: candidates taken from each of vector and keyword search, and the
    # (estimated) token budget for the returned results.
    search_candidates: int = 50
    search_token_budget: int = 4_000
    # Tool outputs go straight into an LLM's context, so every one is capped.
    tool_max_chars: int = 12_000
    read_max_lines: int = 200

    @property
    def db_path(self) -> Path:
        return self.home / "grepwise.db"

    @property
    def model_cache_dir(self) -> Path:
        return self.home / "models"


@lru_cache
def get_settings() -> Settings:
    return Settings()
