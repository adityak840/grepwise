"""Retrieval evals: does search put an expected file near the top? (spec 11, section 7)

Hit@5: an expected path is among the top 5 results' paths. MRR (mean reciprocal rank): the
average of 1 / rank of the first result with an expected path (0 if none in the candidates).
"""

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from grepwise.db.connection import Conn
from grepwise.ingest.embed import Embedder
from grepwise.search.hybrid import Mode, search

HIT_AT = 5


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    question: str
    sources: list[str]
    paths: list[str]  # any of these counts as found


@dataclass(frozen=True)
class RetrievalResult:
    id: str
    category: str
    rank: int | None  # 1-based rank of the first result with an expected path
    top_paths: list[str]  # the top 5 results' paths, to see what came up instead

    @property
    def hit(self) -> bool:
        return self.rank is not None and self.rank <= HIT_AT

    @property
    def reciprocal_rank(self) -> float:
        return 1 / self.rank if self.rank else 0.0


def load_questions(path: Path, *, include_drafts: bool = False) -> list[Question]:
    """The answerable questions (not `absent`), skipping drafts unless asked."""
    questions = []
    for q in yaml.safe_load(path.read_text()):
        expect = q.get("expect", {})
        if expect.get("absent") or (q.get("draft") and not include_drafts):
            continue
        questions.append(
            Question(
                id=q["id"],
                category=q["category"],
                question=q["question"],
                sources=list(q["sources"]),
                paths=list(expect.get("paths") or expect.get("paths_all") or []),
            )
        )
    return questions


def run_retrieval(
    conn: Conn,
    embedder: Embedder,
    questions: Sequence[Question],
    *,
    mode: Mode = "hybrid",
    candidates: int = 50,
) -> list[RetrievalResult]:
    results = []
    for q in questions:
        found = search(
            conn, embedder, q.question, sources=q.sources, limit=candidates, token_budget=None,
            candidates=candidates, mode=mode,
        )  # fmt: skip
        paths = [r.path for r in found]
        rank = next((i for i, p in enumerate(paths, 1) if p in q.paths), None)
        results.append(RetrievalResult(q.id, q.category, rank, paths[:HIT_AT]))
    return results


def summarize(results: Sequence[RetrievalResult]) -> dict[str, dict[str, float]]:
    """{'all': {...}, 'code': {...}, ...}, each with n, hit_at_5 and mrr."""
    groups: dict[str, list[RetrievalResult]] = {"all": list(results)}
    for r in results:
        groups.setdefault(r.category, []).append(r)
    return {
        name: {
            "n": len(rs),
            "hit_at_5": sum(r.hit for r in rs) / len(rs),
            "mrr": sum(r.reciprocal_rank for r in rs) / len(rs),
        }
        for name, rs in groups.items()
        if rs
    }


def write_report(results: Sequence[RetrievalResult], mode: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "mode": mode,
        "summary": summarize(results),
        "questions": [asdict(r) for r in results],
    }
    path = out_dir / f"retrieval-{mode}.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    return path
