from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from statistics import mean
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.evals.catalog import GOLDEN_SCENARIOS
from app.models import (
    AgentRun,
    EvalSuiteRun,
    Evaluation,
    Experiment,
    HumanReview,
)

RECENT_RUN_AGENT_TYPES = ("research", "document", "support", "extraction")
RECENT_RUNS_PER_AGENT_TYPE = 2
RECENT_RUN_LIMIT = 8


def _average(values: list[float]) -> float:
    return round(mean(values), 4) if values else 0.0


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
    return ordered[index]


def _representative_recent_runs(runs: list[AgentRun]) -> list[AgentRun]:
    """Return a recent, balanced sample without representing it as a strict timeline."""
    selected: list[AgentRun] = []
    selected_ids: set[str] = set()

    for agent_type in RECENT_RUN_AGENT_TYPES:
        selected_for_agent_type = 0
        for run in runs:
            if run.agent_type != agent_type or run.id in selected_ids:
                continue
            selected.append(run)
            selected_ids.add(run.id)
            selected_for_agent_type += 1
            if selected_for_agent_type == RECENT_RUNS_PER_AGENT_TYPE:
                break

    for run in runs:
        if len(selected) == RECENT_RUN_LIMIT:
            break
        if run.id not in selected_ids:
            selected.append(run)
            selected_ids.add(run.id)

    return sorted(selected, key=lambda run: run.created_at, reverse=True)


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

    latest_experiment = db.scalar(
        select(Experiment)
        .options(selectinload(Experiment.candidates))
        .where(Experiment.status == "completed")
        .order_by(desc(Experiment.completed_at))
        .limit(1)
    )
    comparison = (
        [
            {
                "experiment_id": latest_experiment.id,
                "provider": candidate.provider,
                "model": candidate.model,
                "quality": candidate.quality or 0.0,
                "pass_rate": candidate.pass_rate or 0.0,
                "task_completion": candidate.task_completion or 0.0,
                "latency_ms": candidate.latency_p50_ms or 0,
                "latency_p95_ms": candidate.latency_p95_ms or 0,
                "cost_per_task": candidate.cost_per_task or 0.0,
                "runs": candidate.run_count,
                "baseline_quality": candidate.baseline_quality,
                "quality_delta": candidate.quality_delta,
                "failure_breakdown": candidate.failure_breakdown,
            }
            for candidate in latest_experiment.candidates
        ]
        if latest_experiment
        else []
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

    latest_suite = db.scalar(
        select(EvalSuiteRun)
        .where(EvalSuiteRun.kind == "regression")
        .order_by(desc(EvalSuiteRun.created_at))
        .limit(1)
    )
    deterministic_scenarios = {
        run.scenario_id
        for run in runs
        if run.scenario_id
        and any(
            evaluation.evaluator_type == "deterministic+rubric"
            for evaluation in run.evaluations
        )
    }
    judged_scenarios = {
        run.scenario_id
        for run in runs
        if run.scenario_id
        and any(
            evaluation.evaluator_type.startswith("llm_judge:")
            for evaluation in run.evaluations
        )
    }
    human_review_count = len(db.scalars(select(HumanReview)).all())
    representative_recent_runs = _representative_recent_runs(runs)
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
                {
                    "name": "Deterministic tests",
                    "status": "active" if deterministic_scenarios else "not run",
                    "coverage": len(deterministic_scenarios),
                },
                {"name": "Golden datasets", "status": "defined", "coverage": 218},
                {
                    "name": "LLM-as-judge",
                    "status": "active" if judged_scenarios else "not run",
                    "coverage": len(judged_scenarios),
                },
                {
                    "name": "Rubric scoring",
                    "status": "active" if deterministic_scenarios else "not run",
                    "coverage": len(deterministic_scenarios),
                },
                {
                    "name": "Human evaluation",
                    "status": "active" if human_review_count else "not run",
                    "coverage": human_review_count,
                },
                {
                    "name": "Regression tests",
                    "status": "active" if latest_suite else "not run",
                    "coverage": latest_suite.scenario_count if latest_suite else 0,
                },
            ],
        },
        "latest_suite": (
            {
                "id": latest_suite.id,
                "commit_sha": latest_suite.commit_sha,
                "scenario_count": latest_suite.scenario_count,
                "success_rate": latest_suite.success_rate,
                "previous_success_rate": latest_suite.previous_success_rate,
                "baseline_source": latest_suite.baseline_source,
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
            for run in representative_recent_runs
        ],
    }
