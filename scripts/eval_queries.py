#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean

from app.agent import QaAgent
from app.config import Settings


@dataclass
class EvalCase:
    name: str
    query: str
    required_terms: list[str]


@dataclass
class EvalResult:
    name: str
    passed: bool
    score: float
    latency_ms: int
    tool_calls: int
    missing_terms: list[str]
    answer: str


EVAL_CASES: list[EvalCase] = [
    EvalCase(
        name="taxonomy_rollout_blueharbor",
        query=(
            "which customer's issue started after the 2026-02-20 taxonomy rollout, "
            "and what proof plan did we propose to get them comfortable with renewal?"
        ),
        required_terms=[
            "blueharbor logistics",
            "7-10 business day",
            "a/b test",
            "top 20 saved searches",
            "80%",
        ],
    ),
    EvalCase(
        name="verdant_patch_window",
        query=(
            "for Verdant Bay, what's the approved live patch window, and exactly how do we "
            "roll back if the validation checks fail?"
        ),
        required_terms=[
            "2026-03-24",
            "02:00",
            "04:00",
            "orchestrator rollback",
            "replays the invalidation hook",
        ],
    ),
    EvalCase(
        name="mapleharvest_transform",
        query=(
            "in the MapleHarvest Quebec pilot, what temporary field mappings are we planning in "
            "the router transform, and what is the March 23 workshop supposed to produce?"
        ),
        required_terms=[
            "txn_id",
            "transaction_id",
            "total_amount",
            "amount_cents",
            "2026-03-23",
            "signed schema document",
        ],
    ),
]


def _score_answer(answer: str, required_terms: list[str]) -> tuple[float, list[str]]:
    lowered = answer.lower()
    missing = [term for term in required_terms if term.lower() not in lowered]
    score = (len(required_terms) - len(missing)) / max(1, len(required_terms))
    return score, missing


def run_eval(out_path: Path) -> dict:
    settings = Settings.from_env()
    agent = QaAgent.build(settings)

    results: list[EvalResult] = []
    for case in EVAL_CASES:
        try:
            run = agent.answer_with_metrics(case.query, conversation_id=f"eval:{case.name}")
            answer = run["answer"]
            latency_ms = int(run["latency_ms"])
            tool_calls = int(run["tool_calls"])
        except Exception as exc:
            answer = f"EVAL_ERROR: {exc}"
            latency_ms = -1
            tool_calls = -1

        score, missing = _score_answer(answer, case.required_terms)
        results.append(
            EvalResult(
                name=case.name,
                passed=score >= 0.8,
                score=score,
                latency_ms=latency_ms,
                tool_calls=tool_calls,
                missing_terms=missing,
                answer=answer,
            )
        )

    valid_latencies = [r.latency_ms for r in results if r.latency_ms >= 0]
    valid_tool_calls = [r.tool_calls for r in results if r.tool_calls >= 0]
    summary = {
        "total_cases": len(results),
        "passed_cases": sum(1 for r in results if r.passed),
        "avg_score": round(mean(r.score for r in results), 3),
        "avg_latency_ms": int(mean(valid_latencies)) if valid_latencies else -1,
        "avg_tool_calls": round(mean(valid_tool_calls), 2) if valid_tool_calls else -1,
    }
    payload = {
        "summary": summary,
        "results": [asdict(r) for r in results],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run assignment query evals.")
    parser.add_argument(
        "--out",
        default="state/eval_results.json",
        help="Path to JSON output file",
    )
    args = parser.parse_args()
    payload = run_eval(Path(args.out))
    print(json.dumps(payload["summary"], indent=2))


if __name__ == "__main__":
    main()
