"""Measuring and enforcing the size of tool outputs."""

import json
from typing import Any

# Room kept for JSON keys, citations and other small fields when sizing content.
OVERHEAD_CHARS = 1_000


def serialize(output: Any) -> str:
    """Exactly what a caller (an LLM) sees: compact JSON."""
    return json.dumps(output, ensure_ascii=False, separators=(",", ":"))


def size(output: Any) -> int:
    return len(serialize(output))


def shrink_to_fit(output: dict[str, Any], max_chars: int) -> dict[str, Any]:
    """Last-resort cap applied by call_tool after each tool's own capping: drop items from the
    longest list, else cut the longest string, until the JSON fits. Marks `truncated`."""
    if size(output) <= max_chars:
        return output
    output = json.loads(serialize(output))  # a deep copy we can edit
    output["truncated"] = True
    while size(output) > max_chars:
        lists: list[list[Any]] = []
        strings: list[tuple[Any, Any, str]] = []
        _collect(output, lists, strings)
        longest_list = max(lists, key=len, default=None)
        if longest_list is not None and len(longest_list) > 1:
            longest_list.pop()
            continue
        if not strings:
            break
        holder, key, value = max(strings, key=lambda s: len(s[2]))
        excess = size(output) - max_chars
        if len(value) <= 20:
            break
        holder[key] = value[: max(0, len(value) - excess - 20)] + "… (truncated)"
    return output


def _collect(node: Any, lists: list[list[Any]], strings: list[tuple[Any, Any, str]]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str):
                strings.append((node, k, v))
            else:
                _collect(v, lists, strings)
    elif isinstance(node, list):
        lists.append(node)
        for i, v in enumerate(node):
            if isinstance(v, str):
                strings.append((node, i, v))
            else:
                _collect(v, lists, strings)
