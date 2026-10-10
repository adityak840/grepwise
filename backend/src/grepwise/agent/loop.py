"""The agent loop: the model calls tools until it can answer, then citations are checked.

The agent touches the world only through M3's `call_tool` (read-only). Tool output goes only
into `tool` messages, never into the system or user message.
"""

import json
import logging
import time
from collections.abc import Generator, Sequence
from dataclasses import dataclass, field
from typing import Any

from grepwise.agent.citations import MARKER, SeenRanges, check_citations
from grepwise.agent.events import Event, summarize
from grepwise.agent.llm import (
    LLMClient,
    LLMError,
    Message,
    TextDelta,
    ToolCallPending,
    ToolCallRequest,
    Usage,
)
from grepwise.agent.prompt import CITE_NUDGE, PROMPT_VERSION, SYSTEM_PROMPT
from grepwise.agent.runs import RunRecord, save_run
from grepwise.tools import ToolContext, call_tool, serialize, tool_schemas

log = logging.getLogger(__name__)

# Text that arrives before any tool call is held back until this many characters, so a
# "Let me search..." preamble isn't streamed as if it were the answer.
_HOLD_CHARS = 200
_SOURCE_TOOLS = ("search", "find_symbol")  # tools that take a `sources` filter

FINAL_NUDGE = (
    "You have used all your tool calls. Answer now, using only what the tools have already "
    "shown, with citations. If that isn't enough, say what is missing."
)
BAD_JSON = {
    "error": {
        "code": "bad_input",
        "message": "The tool arguments were not valid JSON.",
        "hint": "Call the tool again with a JSON object of arguments.",
    }
}
TOO_MANY_CALLS = {
    "error": {
        "code": "too_many_calls",
        "message": "Too many tool calls in one step; this one was not run.",
        "hint": "Ask for fewer tools at a time; use the results you already have first.",
    }
}
ALREADY_CALLED = {
    "error": {
        "code": "bad_input",
        "message": "You already made this exact call in this conversation.",
        "hint": "Use the earlier result, or try something different.",
    }
}


def openai_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tool_schemas()
    ]


@dataclass
class _Reply:
    text: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    usage: Usage | None = None


def _estimate_tokens(messages: Sequence[Message]) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) // 4


def _stream_reply(
    llm: LLMClient, messages: list[Message], tools: list[dict[str, Any]] | None
) -> Generator[Event, None, _Reply]:
    """Stream one model reply, yielding `token` events for answer text.

    If the reply turns into a tool call after some text was already streamed, that text was a
    preamble, not the answer: a `reset` event tells the client to discard it."""
    reply = _Reply()
    held, holding, streamed = "", True, False
    for ev in llm.stream(messages, tools):
        if isinstance(ev, TextDelta):
            reply.text += ev.text
            if holding:
                held += ev.text
                if len(held) >= _HOLD_CHARS:
                    yield Event("token", {"text": held})
                    held, holding, streamed = "", False, True
            else:
                yield Event("token", {"text": ev.text})
        elif isinstance(ev, (ToolCallPending, ToolCallRequest)):
            if streamed:
                yield Event("reset", {})
            held, holding, streamed = "", True, False
            if isinstance(ev, ToolCallRequest):
                reply.tool_calls.append(ev)
        elif isinstance(ev, Usage):
            reply.usage = ev
    if held and not reply.tool_calls:
        yield Event("token", {"text": held})
    return reply


def _assistant_message(reply: _Reply) -> Message:
    calls = []
    for tc in reply.tool_calls:
        call: dict[str, Any] = {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.name, "arguments": tc.arguments},
        }
        if tc.extra_content:
            call["extra_content"] = tc.extra_content  # sent back verbatim (Gemini signatures)
        calls.append(call)
    return {"role": "assistant", "content": reply.text or None, "tool_calls": calls}


def _parse_args(raw: str) -> dict[str, Any] | None:
    try:
        args = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    return args if isinstance(args, dict) else None


def _run_tool(
    ctx: ToolContext,
    tc: ToolCallRequest,
    sources: Sequence[str] | None,
    already: set[str],
    seen: SeenRanges,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Run one tool call: (the arguments used, the result the model will see)."""
    args = _parse_args(tc.arguments)
    if args is None:
        return None, BAD_JSON
    if sources and tc.name in _SOURCE_TOOLS and not args.get("sources"):
        args["sources"] = list(sources)  # the caller's source filter applies to every search
    key = f"{tc.name}:{json.dumps(args, sort_keys=True)}"
    if key in already:
        return args, ALREADY_CALLED
    already.add(key)
    result = call_tool(ctx, tc.name, args)
    seen.add_result(tc.name, result)
    return args, result


def run_agent(
    ctx: ToolContext,
    llm: LLMClient,
    question: str,
    *,
    history: Sequence[Message] = (),
    sources: Sequence[str] | None = None,
    max_steps: int | None = None,
    max_input_tokens: int | None = None,
    max_tool_calls_per_step: int | None = None,
    byo_key: bool = False,
    citation_retry: bool | None = None,
) -> Generator[Event, None, None]:
    """Run one question to completion, yielding events. Closing the generator (e.g. a client
    disconnect) stops the run; every run, however it ends, is logged to agent_runs."""
    max_steps = max_steps or ctx.settings.agent_max_steps
    max_input_tokens = max_input_tokens or ctx.settings.agent_max_input_tokens
    max_calls = max_tool_calls_per_step or ctx.settings.agent_max_tool_calls_per_step
    retry_citations = (
        ctx.settings.agent_citation_retry if citation_retry is None else citation_retry
    )
    retried = False
    started = time.monotonic()
    run = RunRecord(question, llm.provider, llm.model, PROMPT_VERSION, byo_key=byo_key)
    seen = SeenRanges()
    already: set[str] = set()
    step_n = 0
    saved = False

    user = question
    if sources:
        user += "\n\n(Only use these sources: " + ", ".join(sources) + ")"
    messages: list[Message] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *({"role": m["role"], "content": m["content"]} for m in history),
        {"role": "user", "content": user},
    ]
    tools = openai_tools()

    def account(reply: _Reply, sent: list[Message]) -> None:
        if reply.usage:
            run.input_tokens += reply.usage.input_tokens
            run.output_tokens += reply.usage.output_tokens
        else:  # some providers don't report usage when streaming
            run.input_tokens += _estimate_tokens(sent)
            run.output_tokens += len(reply.text) // 4

    try:
        answer: str | None = None
        for _ in range(max_steps):
            reply = yield from _stream_reply(llm, messages, tools)
            account(reply, messages)
            if not reply.tool_calls:
                answer = reply.text
                break
            messages.append(_assistant_message(reply))
            for i, tc in enumerate(reply.tool_calls):
                step_n += 1
                if i < max_calls:
                    args, result = _run_tool(ctx, tc, sources, already, seen)
                else:  # still answered: the API needs a tool message for every call id
                    args, result = _parse_args(tc.arguments), TOO_MANY_CALLS
                yield Event("step", {"n": step_n, "tool": tc.name, "args": args})
                ok, summary = summarize(tc.name, result)
                run.steps.append({"tool": tc.name, "args": args, "ok": ok, "summary": summary})
                yield Event(
                    "tool_result", {"n": step_n, "tool": tc.name, "ok": ok, "summary": summary}
                )
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": serialize(result)}
                )
            if run.input_tokens > max_input_tokens:
                log.info("Input token budget (%s) reached; asking for an answer", max_input_tokens)
                break

        if answer is None:  # out of steps or budget: one last call, tools off
            messages.append({"role": "user", "content": FINAL_NUDGE})
            reply = yield from _stream_reply(llm, messages, None)
            account(reply, messages)
            answer = reply.text

        # An answer with no citations, although tools showed citable results: ask once for a
        # rewrite with citations (tools off). Small local models often skip citations otherwise.
        if retry_citations and seen and answer.strip() and not MARKER.search(answer):
            retried = True
            yield Event("reset", {})  # withdraw the uncited draft that was just streamed
            messages += [
                {"role": "assistant", "content": answer},
                {"role": "user", "content": CITE_NUDGE},
            ]
            try:
                reply = yield from _stream_reply(llm, messages, None)
                account(reply, messages)
                rewrite = reply.text
            except LLMError as exc:  # keep the first answer rather than lose everything
                log.warning("Citation rewrite failed (%s); keeping the first answer", exc.code)
                rewrite = ""
                yield Event("reset", {})
            if rewrite.strip():
                answer = rewrite
            else:
                yield Event("token", {"text": answer})  # nothing better: show the draft again

        run.answer = answer
        checks = check_citations(ctx.conn, answer, seen)
        run.citations = [{"citation": c.citation, "status": c.status} for c in checks]
        run.grounded = any(c.status == "verified" for c in checks)
        yield Event("citations", [c.to_event() for c in checks])
        run.seconds = time.monotonic() - started
        run_id = save_run(ctx.conn, run)
        saved = True
        yield Event("done", {
            "run_id": run_id, "steps": step_n, "grounded": run.grounded,
            "input_tokens": run.input_tokens, "output_tokens": run.output_tokens,
            "seconds": round(run.seconds, 2), "provider": llm.provider, "model": llm.model,
            "citation_retry": retried,
        })  # fmt: skip
    except LLMError as exc:
        run.error = f"{exc.code}: {exc.message}"
        yield Event("error", {"code": exc.code, "message": exc.message})
    except GeneratorExit:
        run.error = "cancelled: client disconnected"
        raise
    except Exception:
        log.exception("Agent run failed")
        run.error = "internal error"
        yield Event("error", {"code": "internal", "message": "The agent failed unexpectedly."})
    finally:
        if not saved:
            run.seconds = time.monotonic() - started
            try:
                save_run(ctx.conn, run)
            except Exception:
                log.exception("Could not save the agent run")
