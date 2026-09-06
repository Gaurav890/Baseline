from __future__ import annotations

import os
from collections import Counter

from sqlalchemy.orm import Session

from app.agents.runner import AgentRunner
from app.evals.catalog import GOLDEN_SCENARIOS
from app.models import EvalSuiteRun
from app.providers.registry import provider_registry
from app.schemas import RegressionRequest, RunCreate


async def run_regression_suite(request: RegressionRequest, db: Session) -> EvalSuiteRun:
    runner = AgentRunner()
    selected = GOLDEN_SCENARIOS[: request.limit]
    failures: Counter[str] = Counter()
    latencies: list[int] = []
    costs: list[float] = []
    passed = 0
    for scenario in selected:
        run = await runner.run(
            RunCreate(
                task=scenario.task,
                agent_type=scenario.category,
                provider=request.provider,
                model=request.model,
                context=scenario.context,
                scenario_id=scenario.id,
            ),
            db,
        )
        evaluation = run.evaluations[-1]
        passed += int(evaluation.passed)
        if evaluation.failure_classification:
            failures[evaluation.failure_classification] += 1
        latencies.append(run.latency_ms)
        costs.append(run.cost_usd)

    success_rate = passed / max(1, len(selected))
    ordered_latencies = sorted(latencies)
    p95_index = min(len(ordered_latencies) - 1, round((len(ordered_latencies) - 1) * 0.95))
    suite = EvalSuiteRun(
        commit_sha=os.getenv("GITHUB_SHA", "working-tree")[:64],
        provider=request.provider,
        model=request.model or provider_registry.default_model(request.provider),
        scenario_count=len(selected),
        success_rate=round(success_rate, 4),
        previous_success_rate=request.baseline_success_rate,
        regression_detected=(request.baseline_success_rate - success_rate) > request.threshold,
        failure_breakdown=dict(failures),
        total_cost_usd=round(sum(costs), 4),
        p95_latency_ms=ordered_latencies[p95_index] if ordered_latencies else 0,
    )
    db.add(suite)
    db.commit()
    db.refresh(suite)
    return suite


async def benchmark_providers(
    *, providers: list[str], scenario_limit: int, db: Session
) -> list[dict[str, float | int | str]]:
    results = []
    for provider in providers:
        suite = await run_regression_suite(
            RegressionRequest(
                provider=provider,
                limit=scenario_limit,
                baseline_success_rate=0,
                threshold=0.01,
            ),
            db,
        )
        results.append(
            {
                "provider": provider,
                "model": suite.model,
                "quality": suite.success_rate,
                "cost": suite.total_cost_usd,
                "latency_p95_ms": suite.p95_latency_ms,
                "scenario_count": suite.scenario_count,
            }
        )
    return results

