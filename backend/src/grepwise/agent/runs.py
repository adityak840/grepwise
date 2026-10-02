"""The agent_runs log: one row per run, including failed and cancelled ones."""

from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


@dataclass
class RunRecord:
    question: str
    provider: str
    model: str
    prompt_version: str
    steps: list[dict[str, Any]] = field(default_factory=list)  # [{tool, args, ok, summary}]
    answer: str | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)  # [{citation, status}]
    grounded: bool | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    error: str | None = None


def save_run(conn: psycopg.Connection, run: RunRecord) -> int:
    row = conn.execute(
        """
        INSERT INTO agent_runs (question, provider, model, prompt_version, steps, answer,
                                citations, grounded, input_tokens, output_tokens, seconds, error)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            run.question, run.provider, run.model, run.prompt_version, Jsonb(run.steps),
            run.answer, Jsonb(run.citations), run.grounded, run.input_tokens,
            run.output_tokens, run.seconds, run.error,
        ),
    ).fetchone()  # fmt: skip
    assert row is not None
    return int(row[0])


def recent_runs(conn: psycopg.Connection, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, created_at, question, provider, model, jsonb_array_length(steps), grounded,
               input_tokens, output_tokens, seconds, error
        FROM agent_runs ORDER BY id DESC LIMIT %s
        """,
        (limit,),
    ).fetchall()
    keys = ("id", "created_at", "question", "provider", "model", "steps", "grounded",
            "input_tokens", "output_tokens", "seconds", "error")  # fmt: skip
    return [dict(zip(keys, row, strict=True)) for row in rows]
