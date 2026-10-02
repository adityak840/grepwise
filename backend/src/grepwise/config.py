"""Settings, read from environment variables and the repo-root `.env` file."""

from dataclasses import dataclass
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
    # local: an open-source model run on this machine (no quota). gemini: Google's API (free
    # tier caps at ~1,000 chunks/day). fake: deterministic vectors for tests.
    embedder: Literal["local", "gemini", "fake"] = "local"
    local_embedding_model: str = "nomic-ai/nomic-embed-text-v1.5-Q"
    model_cache_dir: Path = Path.home() / ".cache" / "grepwise" / "fastembed"
    embedding_model: str = "gemini-embedding-001"  # used when embedder = gemini
    embedding_dim: int = 768
    # Stay under Gemini's per-minute token quota (free tier is tight). Raise on a paid key.
    embed_tokens_per_minute: int = 20_000
    # Hybrid search: candidates taken from each of vector and keyword search, and the
    # (estimated) token budget for the returned results.
    search_candidates: int = 50
    search_token_budget: int = 4_000
    # Tool outputs go straight into an LLM's context, so every one is capped.
    tool_max_chars: int = 12_000
    read_max_lines: int = 200

    # The agent's LLM. Both providers speak the OpenAI chat API. LLM_MODEL and LLM_BASE_URL
    # default per provider (see LLM_DEFAULTS); LLM_API_KEY defaults to GEMINI_API_KEY.
    llm_provider: Literal["gemini", "ollama"] = "gemini"
    llm_model: str = ""
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_timeout_seconds: float = 60.0
    # Passed as `reasoning_effort` when set (none | low | medium | high). Default per provider:
    # none for Ollama (qwen's "thinking" is slow and leaks into answers), unset for Gemini.
    llm_reasoning_effort: str = ""
    agent_max_steps: int = 8
    agent_max_input_tokens: int = 60_000
    # Tool calls run per model reply; extra calls get an error result. Stops a confused model
    # from flooding the next request (and a 16k local context) with results.
    agent_max_tool_calls_per_step: int = 4

    # HTTP API
    chat_rate_per_minute: int = 3  # per client IP
    chat_rate_per_day: int = 10  # per client IP
    # Shared by every visitor; keep it safely under the LLM's real daily allowance.
    chat_global_per_day: int = 100
    allowed_origins: str = "http://localhost:3000"  # comma-separated
    # Behind a proxy (Render), the client IP is in X-Forwarded-For. Only trust it there.
    trust_proxy_headers: bool = False

    def llm(self, provider: str | None = None) -> "LLMConfig":
        """Provider, model, URL and key. LLM_MODEL / LLM_BASE_URL only apply to the configured
        provider: `--provider` switching to another one uses that provider's defaults."""
        chosen = provider or self.llm_provider
        own = chosen == self.llm_provider
        base_url, model, effort = LLM_DEFAULTS[chosen]
        key = self.llm_api_key.get_secret_value() or (
            self.gemini_api_key.get_secret_value() if chosen == "gemini" else "ollama"
        )
        return LLMConfig(
            provider=chosen,
            model=(self.llm_model if own and self.llm_model else model),
            base_url=(self.llm_base_url if own and self.llm_base_url else base_url),
            api_key=SecretStr(key),
            timeout=self.llm_timeout_seconds,
            reasoning_effort=(
                self.llm_reasoning_effort if own and self.llm_reasoning_effort else effort
            )
            or None,
        )

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


# Checked October 2026. gemini-3.5-flash-lite: on the free tier, gemini-3.8-flash allows only 20
# requests a day (2-4 questions); flash-lite has its own, larger allowance and answered well in
# live tests. qwen3.5 is tagged "tools" in Ollama's library. grepwise-qwen3.5 is qwen3.5:9b
# with a 16k context (backend/ollama/Modelfile): Ollama's 4k default truncates agent prompts.
GEMINI_OPENAI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
LLM_DEFAULTS = {  # provider: (base URL, model, reasoning effort)
    "gemini": (GEMINI_OPENAI_URL, "gemini-3.5-flash-lite", ""),
    "ollama": ("http://localhost:11434/v1", "grepwise-qwen3.5", "none"),  # see `make ollama`
}


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    base_url: str
    api_key: SecretStr
    timeout: float
    reasoning_effort: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
