from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from statistics import mean
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.evals.catalog import GOLDEN_SCENARIOS
from app.models import AgentRun, EvalSuiteRun, Evaluation


def _average(values: list[float]) -> float:
    return round(mean(values), 4) if values else 0.0


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
    return ordered[index]


def dashboard_snapshot(db: Session) -> dict[str, Any]:
    runs = list(
        db.scalars(
            select(AgentRun)
            .options(selectinload(AgentRun.evaluations))
            .order_by(desc(AgentRun.created_at))
        ).all()
    )
    evaluations = [evaluation for run in runs for evaluation in run.evaluations]
    completed = [run for run in runs if run.status == "completed"]
    failure_breakdown = Counter(
        evaluation.failure_classification
        for evaluation in evaluations
        if evaluation.failure_classification
    )

    by_provider: dict[str, list[tuple[AgentRun, Evaluation]]] = defaultdict(list)
    for run in runs:
        if run.evaluations:
            by_provider[run.provider].append((run, run.evaluations[-1]))

    comparison = []
    for provider, entries in by_provider.items():
        comparison.append(
            {
                "provider": provider,
                "model": entries[0][0].model,
                "quality": _average([evaluation.score for _, evaluation in entries]),
                "task_completion": _average(
                    [evaluation.task_completion for _, evaluation in entries]
                ),
                "latency_ms": round(mean(run.latency_ms for run, _ in entries)),
                "cost_per_task": round(mean(run.cost_usd for run, _ in entries), 4),
                "runs": len(entries),
            }
        )
    comparison.sort(key=lambda row: row["quality"], reverse=True)

    daily: dict[str, list[tuple[AgentRun, Evaluation]]] = defaultdict(list)
    for run in runs:
        if run.evaluations:
            daily[run.created_at.date().isoformat()].append((run, run.evaluations[-1]))
    trend = [
        {
            "date": day,
            "quality": _average([evaluation.score for _, evaluation in entries]),
            "latency_ms": round(mean(run.latency_ms for run, _ in entries)),
            "runs": len(entries),
        }
        for day, entries in sorted(daily.items())
    ]

    latest_suite = db.scalar(select(EvalSuiteRun).order_by(desc(EvalSuiteRun.created_at)).limit(1))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "quality": {
            "task_completion": _average([evaluation.task_completion for evaluation in evaluations]),
            "answer_correctness": _average(
                [evaluation.answer_correctness for evaluation in evaluations]
            ),
            "tool_selection_accuracy": _average(
                [evaluation.tool_selection_accuracy for evaluation in evaluations]
            ),
            "hallucination_rate": _average(
                [evaluation.hallucination_rate for evaluation in evaluations]
            ),
            "failure_rate": (
                round(1 - len(completed) / len(runs), 4) if runs else 0.0
            ),
            "eval_pass_rate": round(
                sum(evaluation.passed for evaluation in evaluations) / max(1, len(evaluations)), 4
            ),
        },
        "operations": {
            "runs": len(runs),
            "latency_p50_ms": _percentile([run.latency_ms for run in runs], 0.50),
            "latency_p95_ms": _percentile([run.latency_ms for run in runs], 0.95),
            "tokens": sum(run.input_tokens + run.output_tokens for run in runs),
            "cost_total": round(sum(run.cost_usd for run in runs), 4),
            "cost_per_task": round(sum(run.cost_usd for run in runs) / max(1, len(runs)), 4),
            "retries": sum(run.retries for run in runs),
            "escalation_rate": round(sum(run.escalated for run in runs) / max(1, len(runs)), 4),
        },
        "model_comparison": comparison,
        "trend": trend,
        "failure_breakdown": dict(failure_breakdown),
        "evaluation": {
            "scenario_count": len(GOLDEN_SCENARIOS),
            "methods": [
                {"name": "Deterministic tests", "status": "active", "coverage": 218},
                {"name": "Golden datasets", "status": "active", "coverage": 218},
                {"name": "LLM-as-judge", "status": "available", "coverage": 72},
                {"name": "Rubric scoring", "status": "active", "coverage": 218},
                {"name": "Human evaluation", "status": "active", "coverage": 34},
                {"name": "Regression tests", "status": "active", "coverage": 218},
            ],
        },
        "latest_suite": (
            {
                "id": latest_suite.id,
                "commit_sha": latest_suite.commit_sha,
                "scenario_count": latest_suite.scenario_count,
                "success_rate": latest_suite.success_rate,
                "previous_success_rate": latest_suite.previous_success_rate,
                "regression_detected": latest_suite.regression_detected,
                "failure_breakdown": latest_suite.failure_breakdown,
                "total_cost_usd": latest_suite.total_cost_usd,
                "p95_latency_ms": latest_suite.p95_latency_ms,
                "created_at": latest_suite.created_at.isoformat(),
            }
            if latest_suite
            else None
        ),
        "recent_runs": [
            {
                "id": run.id,
                "task": run.task,
                "agent_type": run.agent_type,
                "provider": run.provider,
                "model": run.model,
                "status": run.status,
                "score": run.evaluations[-1].score if run.evaluations else None,
                "failure": (
                    run.evaluations[-1].failure_classification if run.evaluations else None
                ),
                "latency_ms": run.latency_ms,
                "cost_usd": run.cost_usd,
                "created_at": run.created_at.isoformat(),
            }
            for run in runs[:8]
        ],
    }
