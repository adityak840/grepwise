"""LLM providers. Gemini and Ollama both speak the OpenAI chat-completions API, so one client
(the official `openai` package) handles either; `ScriptedClient` replays fixed replies in tests.
"""

import logging
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import openai

from grepwise.config import LLMConfig

log = logging.getLogger(__name__)

RETRY_DELAYS = (2.0, 4.0, 8.0)  # seconds; one retry after each, on 429 and 5xx
MAX_RETRY_AFTER = 60.0

Message = dict[str, Any]


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallPending:
    """The reply has started a tool call: whatever text came before it is not the answer."""


@dataclass(frozen=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: str  # JSON text, as the model wrote it
    # Provider data that must be sent back with this call, e.g. Gemini's
    # {"google": {"thought_signature": ...}}; Gemini rejects the next request without it.
    extra_content: dict[str, Any] | None = None


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


LLMEvent = TextDelta | ToolCallPending | ToolCallRequest | Usage


class LLMError(Exception):
    """Ends the agent run. `code`: rate_limited, quota_exhausted, timeout, unavailable,
    provider_error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class LLMClient(Protocol):
    provider: str
    model: str

    def stream(
        self, messages: Sequence[Message], tools: list[dict[str, Any]] | None
    ) -> Iterator[LLMEvent]: ...


def _error_body(exc: openai.APIStatusError) -> dict[str, Any]:
    """The provider's error object; Gemini wraps it in a one-item list."""
    body: Any = exc.body
    if isinstance(body, list) and body:
        body = body[0]
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        body = body["error"]
    return dict(body) if isinstance(body, dict) else {}


def _retry_after(exc: openai.APIStatusError) -> float | None:
    """Seconds to wait: the Retry-After header, or Gemini's RetryInfo ('13s') in the body."""
    value: Any = exc.response.headers.get("retry-after")
    if value is None:
        for detail in _error_body(exc).get("details") or []:
            if isinstance(detail, dict) and str(detail.get("@type", "")).endswith("RetryInfo"):
                value = str(detail.get("retryDelay", "")).rstrip("s")
    try:
        return min(float(value) + 1, MAX_RETRY_AFTER) if value else None  # +1: round up
    except ValueError:
        return None


def _short_message(exc: openai.APIStatusError) -> str:
    """The first line of the provider's message, not the whole JSON body."""
    message = str(_error_body(exc).get("message") or exc.message)
    return message.strip().splitlines()[0][:300]


def _is_daily_quota(exc: openai.APIStatusError) -> bool:
    text = f"{exc.message} {exc.body}".lower()
    return "perday" in text or "per day" in text or "daily" in text


class OpenAICompatibleClient:
    def __init__(
        self,
        config: LLMConfig,
        *,
        client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.provider = config.provider
        self.model = config.model
        self.base_url = config.base_url
        self.timeout = config.timeout
        self.reasoning_effort = config.reasoning_effort
        self.sleep = sleep
        # max_retries=0: we retry ourselves, with our own schedule and Retry-After handling.
        self.client = client or openai.OpenAI(
            api_key=config.api_key.get_secret_value(),
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=0,
        )

    def _create(self, messages: Sequence[Message], tools: list[dict[str, Any]] | None) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
            "timeout": self.timeout,
        }
        if tools:
            kwargs["tools"] = tools
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                return self.client.chat.completions.create(**kwargs)
            except openai.APITimeoutError:
                raise LLMError(
                    "timeout", f"{self.provider} took longer than {self.timeout:.0f}s."
                ) from None
            except openai.APIConnectionError:
                raise LLMError(
                    "unavailable", f"Could not reach {self.provider} at {self.base_url}."
                ) from None
            except openai.APIStatusError as exc:
                if exc.status_code == 429 and _is_daily_quota(exc):
                    raise LLMError(
                        "quota_exhausted",
                        f"The {self.provider} daily quota is used up; try again tomorrow.",
                    ) from None
                retryable = exc.status_code == 429 or exc.status_code >= 500
                if not retryable or attempt == len(RETRY_DELAYS):
                    code = "rate_limited" if exc.status_code == 429 else "provider_error"
                    message = f"{self.provider} returned {exc.status_code}: {_short_message(exc)}"
                    raise LLMError(code, message) from None
                delay = _retry_after(exc) or RETRY_DELAYS[attempt]
                log.warning(
                    "%s returned %s; retrying in %.0fs", self.provider, exc.status_code, delay
                )
                self.sleep(delay)
        raise AssertionError("unreachable")

    def stream(
        self, messages: Sequence[Message], tools: list[dict[str, Any]] | None
    ) -> Iterator[LLMEvent]:
        response = self._create(messages, tools)
        calls: dict[int, dict[str, Any]] = {}
        try:
            for chunk in response:
                if chunk.usage:
                    yield Usage(chunk.usage.prompt_tokens or 0, chunk.usage.completion_tokens or 0)
                for choice in chunk.choices:
                    delta = choice.delta
                    if delta.content:
                        yield TextDelta(delta.content)
                    for tc in delta.tool_calls or []:
                        if not calls:
                            yield ToolCallPending()
                        call = calls.setdefault(
                            tc.index, {"id": "", "name": "", "arguments": "", "extra": None}
                        )
                        call["id"] = tc.id or call["id"]
                        if tc.function:
                            call["name"] += tc.function.name or ""
                            call["arguments"] += tc.function.arguments or ""
                        extra = (tc.model_extra or {}).get("extra_content")
                        if extra:
                            call["extra"] = extra
        except openai.APITimeoutError:
            raise LLMError("timeout", f"{self.provider} stopped responding.") from None
        except openai.APIError as exc:
            raise LLMError("provider_error", f"{self.provider} stream failed: {exc}") from None
        for _, call in sorted(calls.items()):
            yield ToolCallRequest(
                id=call["id"] or f"call_{uuid.uuid4().hex[:12]}",
                name=call["name"],
                arguments=call["arguments"] or "{}",
                extra_content=call["extra"],
            )


Script = Sequence[LLMEvent] | Exception


class ScriptedClient:
    """Replays one script per `stream()` call, recording what it was sent. For tests."""

    def __init__(
        self, scripts: Iterable[Script], provider: str = "scripted", model: str = "scripted-1"
    ):
        self.scripts = list(scripts)
        self.provider = provider
        self.model = model
        self.calls: list[tuple[list[Message], list[dict[str, Any]] | None]] = []

    def stream(
        self, messages: Sequence[Message], tools: list[dict[str, Any]] | None
    ) -> Iterator[LLMEvent]:
        self.calls.append(([dict(m) for m in messages], tools))
        if not self.scripts:
            raise AssertionError("ScriptedClient ran out of scripts")
        script = self.scripts.pop(0)
        if isinstance(script, Exception):
            raise script
        yield from script


def make_llm(config: LLMConfig) -> LLMClient:
    return OpenAICompatibleClient(config)


def text(answer: str) -> list[LLMEvent]:
    """Script helper: a reply that is just text."""
    return [TextDelta(answer), Usage(100, 20)]


def tool_call(name: str, arguments: str, call_id: str | None = None) -> list[LLMEvent]:
    """Script helper: a reply that calls one tool."""
    return [
        ToolCallPending(),
        ToolCallRequest(call_id or f"call_{name}", name, arguments),
        Usage(100, 10),
    ]
