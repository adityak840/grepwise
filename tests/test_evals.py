"""The answer eval (with a fake `claude` runner) and the resolution eval (fake resolver)."""

import json
from pathlib import Path
from typing import Any

from grepwise.db.connection import Conn
from grepwise.evals import run_resolution
from grepwise.evals_answers import (
    AnswerQuestion,
    claude_args,
    prompt_for,
    run_answers,
    score,
    summarize_answers,
)
from grepwise.resolve import Resolution, ResolveError

Q = AnswerQuestion(
    id="code-01", category="code", question="How does expiry work?",
    sources=["pallets/itsdangerous@2.2.0"], paths=["src/itsdangerous/timed.py"],
    all_paths=False, facts=["max_age", "SignatureExpired"], absent=False,
)  # fmt: skip
ABSENT = AnswerQuestion("absent-02", "code", "How do I encrypt?", ["pallets/itsdangerous@2.2.0"],
                        [], False, [], True)  # fmt: skip


def test_score() -> None:
    assert score(Q, "Uses MAX_AGE and raises SignatureExpired (src/itsdangerous/timed.py)") == (
        1.0, True, None,
    )  # fmt: skip
    assert score(Q, "It checks max_age.") == (0.5, False, None)
    both = AnswerQuestion("x", "cross", "q", [], ["a.md", "b.py"], True, [], False)
    assert score(both, "see a.md")[1] is False and score(both, "a.md and b.py")[1] is True
    assert score(ABSENT, "itsdangerous only signs data; it doesn't provide encryption.")[2] is True
    assert score(ABSENT, "Use Fernet like this: ...")[2] is False
    assert score(ABSENT, "None. Click has no decorator for that.")[2] is True
    assert score(ABSENT, "Here's how: first, none of the defaults...")[2] is False


def test_prompt_and_args_are_the_same_apart_from_grepwise(tmp_path: Path) -> None:
    assert "pallets/itsdangerous@2.2.0" in prompt_for(Q) and "How does expiry work?" in prompt_for(
        Q
    )
    without = claude_args("p", "without", tmp_path / "m.json", "sonnet")
    with_ = claude_args("p", "with", tmp_path / "m.json", "sonnet")
    assert "--strict-mcp-config" in without and "--strict-mcp-config" in with_

    def allowed(args: list[str]) -> str:
        return args[args.index("--allowedTools") + 1]

    assert "mcp__grepwise__search" in allowed(with_) and "grepwise" not in allowed(without)
    assert (
        without[without.index("--disallowedTools") + 1]
        == with_[with_.index("--disallowedTools") + 1]
    )


def test_run_answers_saves_and_resumes(tmp_path: Path) -> None:
    calls: list[tuple[list[str], Path]] = []

    def fake_claude(args: list[str], cwd: Path) -> dict[str, Any]:
        calls.append((args, cwd))
        config = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())
        has_grepwise = "grepwise" in config["mcpServers"]
        answer = (
            "max_age, SignatureExpired, src/itsdangerous/timed.py" if has_grepwise else "max_age"
        )
        return {"result": answer, "total_cost_usd": 0.1, "duration_ms": 2000, "num_turns": 3}

    results = run_answers([Q], ["without", "with"], tmp_path, ["/bin/grepwise", "mcp"], "sonnet",
                          runner=fake_claude)  # fmt: skip
    assert [(r.condition, r.fact_coverage, r.path_hit) for r in results] == [
        ("without", 0.5, False), ("with", 1.0, True),
    ]  # fmt: skip
    assert not any((cwd / "pyproject.toml").exists() for _, cwd in calls)  # an empty folder
    again = run_answers([Q], ["without", "with"], tmp_path, ["/bin/grepwise", "mcp"], "sonnet",
                        runner=fake_claude)  # fmt: skip
    assert len(calls) == 2 and [r.fact_coverage for r in again] == [0.5, 1.0]  # resumed
    summary = summarize_answers(again)
    assert summary["with"]["path_hit"] == 1.0 and summary["without"]["fact_coverage"] == 0.5


def test_a_failed_run_is_retried_next_time(tmp_path: Path) -> None:
    def broken(args: list[str], cwd: Path) -> dict[str, Any]:
        raise RuntimeError("usage limit reached")

    (first,) = run_answers([Q], ["with"], tmp_path, ["g", "mcp"], "sonnet", runner=broken)
    assert first.error == "usage limit reached"
    ok = lambda a, c: {"result": "max_age", "num_turns": 1}  # noqa: E731
    (second,) = run_answers([Q], ["with"], tmp_path, ["g", "mcp"], "sonnet", runner=ok)
    assert second.error is None and second.fact_coverage == 0.5


def test_resolution_eval(conn: Conn, tmp_path: Path) -> None:
    packages = tmp_path / "resolution.yaml"
    packages.write_text(
        "- {package: good, version: '1.0', repo: https://github.com/o/Good}\n"
        "- {package: wrong, version: '1.0', repo: https://github.com/o/right}\n"
        "- {package: gone, version: '1.0', repo: https://github.com/o/gone}\n"
    )

    class Fake:
        def resolve(self, package: str, version: str) -> Resolution:
            if package == "gone":
                raise ResolveError("No tag")
            return Resolution(package, version, f"https://github.com/o/{package}", "v1.0")

    results = run_resolution(conn, packages, resolver_factory=lambda c: Fake())
    assert [(r.package, r.ok) for r in results] == [
        ("good", True),
        ("wrong", False),
        ("gone", False),
    ]
    assert results[2].error == "No tag"


def test_parse_stream_counts_tool_calls() -> None:
    from grepwise.evals_answers import parse_stream

    lines = [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Looking."},
            {"type": "tool_use", "name": "mcp__grepwise__search", "input": {}},
        ]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__grepwise__search", "input": {}},
            {"type": "tool_use", "name": "WebFetch", "input": {}},
        ]}},
        {"type": "result", "result": "answer", "total_cost_usd": 0.1, "num_turns": 3},
    ]  # fmt: skip
    out = parse_stream("\n".join(json.dumps(x) for x in lines))
    assert out["result"] == "answer"
    assert out["tools"] == {"mcp__grepwise__search": 2, "WebFetch": 1}
