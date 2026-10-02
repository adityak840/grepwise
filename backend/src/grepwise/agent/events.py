"""The event stream shared by the CLI and SSE. Events carry summaries, never full tool output."""

import json
from dataclasses import dataclass
from typing import Any, Literal

# reset: discard the tokens streamed so far (they were a preamble before a tool call).
EventType = Literal["step", "tool_result", "token", "reset", "citations", "done", "error"]


@dataclass(frozen=True)
class Event:
    type: EventType
    data: Any

    def to_sse(self) -> str:
        """Server-Sent Events wire format: one `event:` line and one JSON `data:` line."""
        payload = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"))
        return f"event: {self.type}\ndata: {payload}\n\n"


def summarize(tool: str, result: dict[str, Any]) -> tuple[bool, str]:
    """(ok, short summary) of a call_tool result, e.g. (True, '6 results')."""
    if set(result) == {"error"}:
        return False, str(result["error"].get("code", "error"))

    def plural(n: int, word: str, many: str | None = None) -> str:
        return f"{n} {word if n == 1 else many or word + 's'}"

    if tool == "list_sources":
        return True, plural(len(result.get("sources", [])), "source")
    if tool == "search":
        return True, plural(len(result.get("results", [])), "result")
    if tool == "find_symbol":
        return True, plural(len(result.get("matches", [])), "match", "matches")
    if tool == "get_endpoint":
        return True, plural(len(result.get("endpoints", [])), "endpoint")
    if tool == "read_file":
        start, end = result.get("start_line"), result.get("end_line")
        return True, f"lines {start}\N{EN DASH}{end} of {result.get('total_lines')}"
    return True, "ok"
