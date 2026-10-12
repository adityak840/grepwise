"""Answer evals: Claude Code with vs. without Grepwise, on the eval questions (spec 15).

Each question is asked through headless Claude Code (`claude -p`) twice, in an empty folder:
  - without: web search and fetch (what Claude Code can look up today);
  - with:    the same, plus Grepwise's MCP server.
File, shell and editing tools are blocked in both, and `--strict-mcp-config` keeps the user's own
MCP servers out. Answers are scored automatically: expected facts mentioned, an expected file
referenced (citation or link), and honesty on questions the sources can't answer.
Results are saved per question, so a run can stop (quota, Ctrl+C) and resume.

The "files" conditions (spec 17) ask in a real project instead: Typer, Click and itsdangerous,
plus httpx, tenacity and attrs (which Grepwise hasn't indexed), installed in its .venv; file tools
(Read, Grep, Glob) and the web allowed, so Claude can read the installed source, as it can in your
projects:
  - files:          nothing else;
  - files-hint:     plus a one-line CLAUDE.md saying the library source is in .venv;
  - files-grepwise: plus Grepwise and a one-line CLAUDE.md saying to use it.
The projects live in the temp folder, and file tools are denied the home folder, so a search can't
wander through the user's disk (macOS protects folders like Music and asks about them).
"""

import json
import re
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

Condition = Literal["with", "without", "files", "files-hint", "files-grepwise"]
FILE_CONDITIONS: tuple[Condition, ...] = ("files", "files-hint", "files-grepwise")
GREPWISE_TOOLS = ["mcp__grepwise__search", "mcp__grepwise__read", "mcp__grepwise__libraries"]
# The baseline is memory plus the web. File tools are blocked too: in an empty folder they
# can only go looking around the user's disk (which made macOS ask for access to other
# apps' data in the first run), and that isn't the comparison meant here.
COMMON_TOOLS = ["WebSearch", "WebFetch"]
BLOCKED_TOOLS = ["Bash", "Read", "Grep", "Glob", "Edit", "Write", "NotebookEdit", "Task"]
FILE_TOOLS = ["Read", "Grep", "Glob"]
# Deny rules for Read also cover Grep and Glob when their path is inside the folder.
OUTSIDE_PROJECT = ["Read(~/**)", "Read(//Users/**)", "Read(//Volumes/**)"]
# The eval sources, plus three libraries Grepwise hasn't indexed (evals/questions_unindexed.yaml).
PROJECT_DEPENDENCIES = [
    "typer==0.27.2", "click==8.5.0", "itsdangerous==2.2.0",
    "httpx==0.28.1", "tenacity==9.2.1", "attrs==26.1.0",
]  # fmt: skip
CLAUDE_MD: dict[str, str] = {
    "files-hint": (
        "- This project's dependencies are installed in `.venv/lib/python*/site-packages/`. "
        "For a question about a library, read its source there (the exact installed version) "
        "instead of answering from memory.\n"
    ),
    "files-grepwise": (
        "- For any question about a third-party library, search it with the Grepwise MCP tools "
        "first and cite the links it returns, even if you think you know the answer.\n"
    ),
}
# Phrases that say the answer isn't there (for `absent` questions).
_NOT_THERE = re.compile(
    r"doesn'?t (provide|support|have|offer|include|exist)|does not (provide|support|have|offer|"
    r"include|exist)|no (built-in|such|support|endpoint|way)|isn'?t (supported|available|part|"
    r"provided|possible)|is not (supported|available|part|provided|possible)|not (supported|"
    r"available|part of|provided)|cannot|can'?t|there is no|there's no|has no|have no|"
    r"^\W*(none|no)\b|only signs|not encrypt",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class AnswerQuestion:
    id: str
    category: str
    question: str
    sources: list[str]
    paths: list[str]  # any one referenced counts (paths_all: every one)
    all_paths: bool
    facts: list[str]
    absent: bool


def load_answer_questions(path: Path, *, include_drafts: bool = False) -> list[AnswerQuestion]:
    out = []
    for q in yaml.safe_load(path.read_text()):
        if q.get("draft") and not include_drafts:
            continue
        expect = q.get("expect", {})
        out.append(
            AnswerQuestion(
                id=q["id"], category=q["category"], question=q["question"],
                sources=list(q["sources"]),
                paths=list(expect.get("paths_all") or expect.get("paths") or []),
                all_paths="paths_all" in expect, facts=[str(f) for f in expect.get("facts", [])],
                absent=bool(expect.get("absent")),
            )
        )  # fmt: skip
    return out


def prompt_for(q: AnswerQuestion) -> str:
    """The same prompt in both conditions: the library and version, then the question."""
    versions = ", ".join(q.sources)
    return (
        f"Question about {versions} (use exactly this version): {q.question}\n\n"
        "Answer concisely. Point to the specific source files or docs pages your answer is "
        "based on. If the library doesn't do what's asked, say so."
    )


@dataclass
class AnswerResult:
    id: str
    category: str
    condition: str
    answer: str
    fact_coverage: float  # share of expected facts mentioned (absent questions: 1.0 or 0.0)
    path_hit: bool  # an expected file referenced (all of them, for paths_all)
    honest: bool | None  # absent questions only: said it isn't there
    cost_usd: float
    seconds: float
    turns: int
    tool_calls: int
    error: str | None = None
    tools: dict[str, int] | None = None  # tool name -> calls, e.g. {"mcp__grepwise__search": 2}


def score(q: AnswerQuestion, answer: str) -> tuple[float, bool, bool | None]:
    text = answer.lower()
    if q.absent:
        honest = bool(_NOT_THERE.search(answer))
        return (1.0 if honest else 0.0), False, honest
    facts = [f for f in q.facts if f.lower() in text]
    coverage = len(facts) / len(q.facts) if q.facts else 1.0
    # An installed package has no src/ folder: '.venv/.../click/core.py' counts for
    # 'src/click/core.py'.
    hits = [p for p in q.paths if p.lower() in text or p.lower().removeprefix("src/") in text]
    path_hit = (len(hits) == len(q.paths)) if q.all_paths else bool(hits)
    return coverage, path_hit, None


Runner = Callable[[list[str], Path], dict[str, Any]]


def run_claude(args: list[str], cwd: Path) -> dict[str, Any]:
    """Run claude with stream-json output: one JSON event per line. Returns the final
    `result` event, plus `tools`: how often each tool was called."""
    done = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=600)
    if done.returncode != 0 and not done.stdout.strip():
        raise RuntimeError(done.stderr.strip()[-500:] or f"claude exited {done.returncode}")
    return parse_stream(done.stdout)


def parse_stream(stdout: str) -> dict[str, Any]:
    tools: dict[str, int] = {}
    result: dict[str, Any] = {}
    for line in stdout.splitlines():
        if not line.strip().startswith("{"):
            continue
        event = json.loads(line)
        if event.get("type") == "assistant":
            for part in event.get("message", {}).get("content", []):
                if isinstance(part, dict) and part.get("type") == "tool_use":
                    tools[part["name"]] = tools.get(part["name"], 0) + 1
        elif event.get("type") == "result":
            result = event
    if not result:
        raise RuntimeError("claude printed no result")
    return {**result, "tools": tools}


def uses_grepwise(condition: Condition) -> bool:
    return condition in ("with", "files-grepwise")


def claude_args(prompt: str, condition: Condition, mcp_config: Path, model: str) -> list[str]:
    files = condition in FILE_CONDITIONS
    allowed = COMMON_TOOLS + (FILE_TOOLS if files else [])
    allowed += GREPWISE_TOOLS if uses_grepwise(condition) else []
    blocked = (
        [t for t in BLOCKED_TOOLS if t not in FILE_TOOLS] + OUTSIDE_PROJECT
        if files
        else (BLOCKED_TOOLS)
    )
    return [
        "claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--model", model,
        "--strict-mcp-config", "--mcp-config", str(mcp_config),
        "--allowedTools", ",".join(allowed), "--disallowedTools", ",".join(blocked),
        # Only the project's settings and CLAUDE.md: the user's own (e.g. a global "use
        # Grepwise" rule) would leak into every condition.
        "--setting-sources", "project",
        "--no-session-persistence",
    ]  # fmt: skip


def prepare_project(base: Path, condition: Condition) -> Path:
    """The project a "files" condition runs in: the eval libraries installed in .venv (once;
    `uv sync` needs the network the first time) and the condition's CLAUDE.md."""
    folder = base / condition
    folder.mkdir(parents=True, exist_ok=True)
    deps = ", ".join(f'"{d}"' for d in PROJECT_DEPENDENCIES)
    pyproject = (
        f'[project]\nname = "app"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n'
        f"dependencies = [{deps}]\n"
    )
    current = folder / "pyproject.toml"
    if not (folder / ".venv").is_dir() or not current.is_file() or current.read_text() != pyproject:
        (folder / "pyproject.toml").write_text(pyproject)
        subprocess.run(["uv", "sync", "--quiet"], cwd=folder, check=True, timeout=600)
    claude_md = folder / "CLAUDE.md"
    if condition in CLAUDE_MD:
        claude_md.write_text(CLAUDE_MD[condition])
    else:
        claude_md.unlink(missing_ok=True)
    return folder


def default_projects_dir() -> Path:
    """Outside the home folder, which the file tools are denied."""
    return Path(tempfile.gettempdir()) / "grepwise-eval-projects"


def ask(
    q: AnswerQuestion,
    condition: Condition,
    grepwise_command: list[str],
    model: str,
    runner: Runner = run_claude,
    projects_dir: Path | None = None,
) -> AnswerResult:
    servers = (
        {"grepwise": {"command": grepwise_command[0], "args": grepwise_command[1:]}}
        if uses_grepwise(condition)
        else {}
    )
    with tempfile.TemporaryDirectory(prefix="grepwise-eval-") as tmp:
        folder = Path(tmp)
        config = folder / "mcp.json"
        config.write_text(json.dumps({"mcpServers": servers}))
        if condition in FILE_CONDITIONS:
            work = prepare_project(projects_dir or default_projects_dir(), condition)
        else:
            work = folder / "project"  # empty: no lockfile, no CLAUDE.md
            work.mkdir()
        try:
            data = runner(claude_args(prompt_for(q), condition, config, model), work)
        except Exception as exc:
            return AnswerResult(q.id, q.category, condition, "", 0.0, False, None, 0.0, 0.0, 0, 0,
                                error=str(exc))  # fmt: skip
    answer = str(data.get("result") or "")
    coverage, path_hit, honest = score(q, answer)
    return AnswerResult(
        id=q.id, category=q.category, condition=condition, answer=answer,
        fact_coverage=coverage, path_hit=path_hit, honest=honest,
        cost_usd=float(data.get("total_cost_usd") or 0.0),
        seconds=float(data.get("duration_ms") or 0) / 1000,
        turns=int(data.get("num_turns") or 0),
        tool_calls=sum((data.get("tools") or {}).values()),
        error=str(data["result"]) if data.get("is_error") else None,
        tools=data.get("tools") or {},
    )  # fmt: skip


def run_answers(
    questions: Sequence[AnswerQuestion],
    conditions: Sequence[Condition],
    out_dir: Path,
    grepwise_command: list[str],
    model: str,
    runner: Runner = run_claude,
    on_result: Callable[[AnswerResult], None] = lambda _: None,
    projects_dir: Path | None = None,
) -> list[AnswerResult]:
    """Ask every question in every condition, skipping ones already saved (resumable)."""
    results = []
    for condition in conditions:
        folder = out_dir / condition
        folder.mkdir(parents=True, exist_ok=True)
        for q in questions:
            path = folder / f"{q.id}.json"
            if path.exists():
                saved = AnswerResult(**json.loads(path.read_text()))
                if saved.error is None:
                    # Rescore: a fix to the scoring applies without asking Claude again.
                    saved.fact_coverage, saved.path_hit, saved.honest = score(q, saved.answer)
                    results.append(saved)
                    continue
            result = ask(q, condition, grepwise_command, model, runner, projects_dir)
            path.write_text(json.dumps(asdict(result), indent=2) + "\n")
            results.append(result)
            on_result(result)
    return results


def summarize_answers(results: Sequence[AnswerResult]) -> dict[str, dict[str, float]]:
    """Per condition: answerable questions' fact coverage and path hits, honesty on absent
    ones, and average cost, time and turns."""
    out: dict[str, dict[str, float]] = {}
    for condition in sorted({r.condition for r in results}):
        rs = [r for r in results if r.condition == condition and r.error is None]
        answerable = [r for r in rs if r.honest is None]
        absent = [r for r in rs if r.honest is not None]
        out[condition] = {
            "questions": len(rs),
            "fact_coverage": _mean([r.fact_coverage for r in answerable]),
            "path_hit": _mean([float(r.path_hit) for r in answerable]),
            "honest_on_absent": _mean([float(bool(r.honest)) for r in absent]),
            "cost_usd": _mean([r.cost_usd for r in rs]),
            "seconds": _mean([r.seconds for r in rs]),
            "turns": _mean([float(r.turns) for r in rs]),
            "used_grepwise": _mean([float(_used(r, "mcp__grepwise")) for r in rs]),
            "used_web": _mean([float(_used(r, "Web")) for r in rs]),
            "used_files": _mean([float(any(_used(r, t) for t in FILE_TOOLS)) for r in rs]),
            "errors": float(sum(1 for r in results if r.condition == condition and r.error)),
        }
    return out


def _used(r: AnswerResult, prefix: str) -> bool:
    return any(name.startswith(prefix) for name in (r.tools or {}))


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0
