from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AgentRun, EvalSuiteRun, Evaluation, TraceSpan

MODEL_FIXTURES = {
    "openai": {
        "model": "gpt-5.6-terra",
        "quality": 0.924,
        "completion": 0.972,
        "correctness": 0.931,
        "tool_accuracy": 0.946,
        "hallucination": 0.018,
        "latency": 2180,
        "cost": 0.032,
    },
    "anthropic": {
        "model": "claude-sonnet-5",
        "quality": 0.918,
        "completion": 0.964,
        "correctness": 0.928,
        "tool_accuracy": 0.951,
        "hallucination": 0.014,
        "latency": 2640,
        "cost": 0.041,
    },
    "gemini": {
        "model": "gemini-3.8-flash",
        "quality": 0.897,
        "completion": 0.941,
        "correctness": 0.904,
        "tool_accuracy": 0.913,
        "hallucination": 0.027,
        "latency": 1430,
        "cost": 0.018,
    },
}


def seed_demo_data(db: Session) -> None:
    if db.scalar(select(func.count()).select_from(AgentRun)):
        return

    now = datetime.now(UTC)
    agents = ["research", "document", "support", "extraction"]
    failure_types = [None, None, None, None, "tool_selection", None, "incorrect_answer"]
    for provider_index, (provider, values) in enumerate(MODEL_FIXTURES.items()):
        for index in range(14):
            adjustment = ((index % 5) - 2) * 0.006
            score = max(0.7, min(0.99, values["quality"] + adjustment))
            failure = failure_types[(index + provider_index) % len(failure_types)]
            passed = failure is None
            created_at = now - timedelta(hours=index * 11 + provider_index * 2)
            latency = int(values["latency"] * (0.88 + (index % 4) * 0.08))
            agent_type = agents[(index + provider_index) % len(agents)]
            run = AgentRun(
                task=f"Golden scenario {agent_type}-{index + 1:03d}",
                agent_type=agent_type,
                provider=provider,
                model=str(values["model"]),
                status="completed",
                planner_output={
                    "strategy": "plan → observe → synthesize → evaluate",
                    "steps": [
                        {"index": 1, "tool": "document_search", "reason": "Ground the response"},
                        {"index": 2, "tool": "validate_schema", "reason": "Verify the result"},
                    ],
                },
                final_output="Verified response grounded in the supplied evaluation fixture.",
                scenario_id=f"{agent_type}-{index + 1:03d}",
                latency_ms=latency,
                input_tokens=760 + index * 13,
                output_tokens=180 + index * 7,
                cost_usd=round(float(values["cost"]) * (0.9 + (index % 3) * 0.1), 4),
                retries=1 if index in (5, 12) else 0,
                escalated=agent_type == "support" and index % 4 == 0,
                created_at=created_at,
                completed_at=created_at + timedelta(milliseconds=latency),
            )
            db.add(run)
            db.flush()
            root = TraceSpan(
                run_id=run.id,
                kind="agent",
                name=f"{agent_type}_agent",
                status="ok",
                started_at=created_at,
                ended_at=run.completed_at,
                duration_ms=latency,
                input={"task": run.task},
                output={"status": "completed", "score": round(score, 3)},
                attributes={"provider": provider, "model": values["model"]},
            )
            db.add(root)
            db.flush()
            span_specs = [
                ("planner", "task_planner", 42),
                ("tool", "document_search", int(latency * 0.24)),
                ("model", str(values["model"]), int(latency * 0.62)),
                ("evaluator", "deterministic+rubric", int(latency * 0.08)),
            ]
            offset = 0
            for kind, name, duration in span_specs:
                span_start = created_at + timedelta(milliseconds=offset)
                db.add(
                    TraceSpan(
                        run_id=run.id,
                        parent_id=root.id,
                        kind=kind,
                        name=name,
                        status="ok" if failure is None or kind != "evaluator" else "error",
                        started_at=span_start,
                        ended_at=span_start + timedelta(milliseconds=duration),
                        duration_ms=duration,
                        input={"fixture": True},
                        output={"recorded": True},
                        attributes={"demo": True},
                    )
                )
                offset += duration
            db.add(
                Evaluation(
                    run_id=run.id,
                    evaluator_type="deterministic+rubric",
                    score=round(score, 4),
                    passed=passed,
                    task_completion=float(values["completion"]),
                    answer_correctness=float(values["correctness"]),
                    tool_selection_accuracy=(
                        0.61 if failure == "tool_selection" else float(values["tool_accuracy"])
                    ),
                    hallucination_rate=float(values["hallucination"]),
                    failure_classification=failure,
                    rubric={
                        "task_completion": {"weight": 0.30},
                        "answer_correctness": {"weight": 0.40},
                        "tool_selection": {"weight": 0.20},
                        "groundedness": {"weight": 0.10},
                    },
                    rationale=(
                        "All evaluation checks passed."
                        if failure is None
                        else f"Failure classified as {failure.replace('_', ' ')}."
                    ),
                    created_at=created_at,
                )
            )

    db.add_all(
        [
            EvalSuiteRun(
                commit_sha="main@previous",
                provider="multi-model",
                model="benchmark-matrix",
                scenario_count=218,
                success_rate=0.912,
                previous_success_rate=0.904,
                regression_detected=False,
                failure_breakdown={
                    "tool_selection": 7,
                    "incorrect_answer": 8,
                    "hallucination": 4,
                },
                total_cost_usd=6.18,
                p95_latency_ms=2810,
                created_at=now - timedelta(days=2),
            ),
            EvalSuiteRun(
                commit_sha="pr/184@8f3c2a1",
                provider="multi-model",
                model="benchmark-matrix",
                scenario_count=218,
                success_rate=0.887,
                previous_success_rate=0.912,
                regression_detected=True,
                failure_breakdown={
                    "tool_selection": 18,
                    "incorrect_answer": 5,
                    "hallucination": 2,
                },
                total_cost_usd=6.02,
                p95_latency_ms=2730,
                created_at=now - timedelta(minutes=18),
            ),
        ]
    )
    db.commit()

