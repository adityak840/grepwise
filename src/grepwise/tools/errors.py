"""Structured tool errors. Tools return these instead of raising for expected problems."""

import functools
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel

ErrorCode = Literal[
    "bad_input",
    "source_not_found",
    "ambiguous_source",
    "file_not_found",
    "bad_line_range",
    "endpoint_not_found",
    "not_reindexed",
    "internal",
]


class ToolErrorBody(BaseModel):
    code: ErrorCode
    message: str
    hint: str  # what the caller should do next


class ToolError(BaseModel):
    error: ToolErrorBody


class ToolFailure(Exception):
    """Raised inside tools; converted to a ToolError at the tool's boundary."""

    def __init__(self, code: ErrorCode, message: str, hint: str):
        super().__init__(message)
        self.error = ToolError(error=ToolErrorBody(code=code, message=message, hint=hint))


def returns_tool_errors[**P, R](fn: Callable[P, R]) -> Callable[P, R | ToolError]:
    """Turn a ToolFailure raised inside `fn` into a returned ToolError."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | ToolError:
        try:
            return fn(*args, **kwargs)
        except ToolFailure as failure:
            return failure.error

    return wrapper
