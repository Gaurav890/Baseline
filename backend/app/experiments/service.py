from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from statistics import mean
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.agents.runner import AgentRunner
from app.config import settings
from app.evals.catalog import GOLDEN_SCENARIOS, SCENARIO_INDEX
from app.models import AgentRun, Evaluation, Experiment, ExperimentCandidate
from app.providers.registry import provider_registry
from app.schemas import ExperimentCreate, RunCreate

DATASET_VERSION = "golden-v1"


class ExperimentConflictError(ValueError):
    """An idempotency key was reused for a different experiment."""


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _request_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_payload(payload).encode()).hexdigest()


def balanced_scenario_ids(limit: int) -> list[str]:
    """Select a deterministic, category-balanced cohort from the frozen catalog."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for scenario in GOLDEN_SCENARIOS:
        grouped[scenario.category].append(scenario.id)
    categories = ("research", "document", "support", "extraction")
    selected: list[str] = []
    index = 0
    while len(selected) < limit:
        made_progress = False
        for category in categories:
            values = grouped[category]
            if index < len(values) and len(selected) < limit:
                selected.append(values[index])
                made_progress = True
        if not made_progress:
            break
        index += 1
    return selected


def _validated_scenario_ids(request: ExperimentCreate) -> list[str]:
    if request.dataset_version != DATASET_VERSION:
        raise ValueError(f"Unknown dataset version: {request.dataset_version}")
    scenario_ids = request.scenario_ids or balanced_scenario_ids(request.scenario_limit)
    unknown = [scenario_id for scenario_id in scenario_ids if scenario_id not in SCENARIO_INDEX]
    if unknown:
        raise ValueError(f"Unknown scenario IDs: {', '.join(unknown[:5])}")
    return scenario_ids


def _validate_candidates(db: Session, request: ExperimentCreate) -> None:
    availability = {
        item["provider"]: bool(item["configured"])
        for item in provider_registry.availability()
    }
    unavailable = [
        candidate.provider
        for candidate in request.candidates
        if not availability.get(candidate.provider, False)
    ]
    if unavailable:
        names = ", ".join(sorted(set(unavailable)))
        raise ValueError(f"Provider credentials are not configured for: {names}")
    if settings.is_production and any(
        candidate.provider == "mock" for candidate in request.candidates
    ):
        raise ValueError("The synthetic mock provider cannot run production experiments")
    calls = len(request.candidates) * len(
        request.scenario_ids or balanced_scenario_ids(request.scenario_limit)
    ) * request.repetitions
    if calls > settings.experiment_max_calls:
        raise ValueError(
            f"Experiment requires {calls} calls; the configured limit is "
            f"{settings.experiment_max_calls}"
        )
    estimated_cost = sum(
        provider_registry.cost(
            candidate.provider,
            settings.experiment_estimated_input_tokens,
            settings.experiment_estimated_output_tokens,
        )
        * (calls / len(request.candidates))
        for candidate in request.candidates
    )
    if estimated_cost > settings.experiment_max_estimated_cost_usd:
        raise ValueError(
            f"Estimated provider cost ${estimated_cost:.2f} exceeds the configured "
            f"${settings.experiment_max_estimated_cost_usd:.2f} limit"
        )
    active = db.scalar(
        select(func.count()).select_from(Experiment).where(
            Experiment.status.in_(("queued", "running"))
        )
    )
    if (active or 0) >= settings.experiment_max_concurrent:
        raise ValueError("The workspace has reached its concurrent experiment limit")


def _previous_baseline(
    db: Session,
    *,
    dataset_version: str,
    scenario_ids: list[str],
    explicit_id: str | None,
) -> Experiment | None:
    if explicit_id:
        baseline = db.scalar(
            select(Experiment)
            .options(selectinload(Experiment.candidates))
            .where(Experiment.id == explicit_id, Experiment.status == "completed")
        )
        if baseline is None:
            raise ValueError("The selected baseline experiment does not exist or is incomplete")
        if baseline.dataset_version != dataset_version or baseline.scenario_ids != scenario_ids:
            raise ValueError("The baseline must use the same dataset version and scenario IDs")
        return baseline

    candidates = db.scalars(
        select(Experiment)
        .options(selectinload(Experiment.candidates))
        .where(
            Experiment.status == "completed",
            Experiment.dataset_version == dataset_version,
        )
        .order_by(desc(Experiment.completed_at))
    ).all()
    return next((item for item in candidates if item.scenario_ids == scenario_ids), None)


def create_experiment(
    db: Session,
    request: ExperimentCreate,
    *,
    idempotency_key: str | None,
) -> tuple[Experiment, bool]:
    scenario_ids = _validated_scenario_ids(request)
    payload = request.model_dump(mode="json") | {"scenario_ids": scenario_ids}
    digest = _request_hash(payload)
    key = idempotency_key or f"implicit:{datetime.now(UTC):%Y%m%d%H}:{digest}"
    existing = db.scalar(
        select(Experiment)
        .options(selectinload(Experiment.candidates))
        .where(Experiment.idempotency_key == key)
    )
    if existing is not None:
        if existing.request_hash != digest:
            raise ExperimentConflictError(
                "Idempotency-Key has already been used for a different experiment"
            )
        return existing, False

    _validate_candidates(db, request)

    baseline = _previous_baseline(
        db,
        dataset_version=request.dataset_version,
        scenario_ids=scenario_ids,
        explicit_id=request.baseline_experiment_id,
    )
    experiment = Experiment(
        name=request.name,
        dataset_version=request.dataset_version,
        scenario_ids=scenario_ids,
        scenario_count=len(scenario_ids),
        repetitions=request.repetitions,
        status="queued",
        baseline_experiment_id=baseline.id if baseline else None,
        idempotency_key=key,
        request_hash=digest,
    )
    for candidate in request.candidates:
        model = candidate.model or provider_registry.default_model(candidate.provider)
        experiment.candidates.append(
            ExperimentCandidate(
                label=candidate.label or model,
                provider=candidate.provider,
                model=model,
            )
        )
    db.add(experiment)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(Experiment)
            .options(selectinload(Experiment.candidates))
            .where(Experiment.idempotency_key == key)
        )
        if existing is None or existing.request_hash != digest:
            raise ExperimentConflictError(
                "Idempotency-Key has already been used for a different experiment"
            ) from None
        return existing, False
    db.refresh(experiment)
    return experiment, True


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
    return ordered[index]


def _matching_baseline_candidate(
    baseline: Experiment | None, candidate: ExperimentCandidate
) -> ExperimentCandidate | None:
    if baseline is None:
        return None
    return next(
        (
            item
            for item in baseline.candidates
            if item.provider == candidate.provider and item.model == candidate.model
        ),
        None,
    )


async def execute_experiment(experiment_id: str, db: Session) -> dict[str, Any]:
    experiment = db.scalar(
        select(Experiment)
        .options(selectinload(Experiment.candidates))
        .where(Experiment.id == experiment_id)
    )
    if experiment is None:
        raise ValueError("Experiment not found")
    if experiment.status == "completed":
        return experiment_result(experiment)

    baseline = None
    if experiment.baseline_experiment_id:
        baseline = db.scalar(
            select(Experiment)
            .options(selectinload(Experiment.candidates))
            .where(Experiment.id == experiment.baseline_experiment_id)
        )
    experiment.status = "running"
    experiment.started_at = experiment.started_at or datetime.now(UTC)
    for candidate in experiment.candidates:
        if candidate.status != "completed":
            candidate.status = "running"
    db.commit()

    runner = AgentRunner()
    try:
        for candidate in experiment.candidates:
            if candidate.status == "completed":
                continue
            runs: list[AgentRun] = []
            evaluations: list[Evaluation] = []
            failures: Counter[str] = Counter()
            for repetition in range(experiment.repetitions):
                for scenario_id in experiment.scenario_ids:
                    scenario = SCENARIO_INDEX[scenario_id]
                    case_key = f"{candidate.id}:{repetition}:{scenario_id}"
                    run = db.scalar(
                        select(AgentRun)
                        .options(selectinload(AgentRun.evaluations))
                        .where(AgentRun.experiment_case_key == case_key)
                    )
                    if run is None:
                        run = await runner.run(
                            RunCreate(
                                task=scenario.task,
                                agent_type=scenario.category,
                                provider=candidate.provider,
                                model=candidate.model,
                                context=scenario.context,
                                scenario_id=scenario.id,
                            ),
                            db,
                            experiment_candidate_id=candidate.id,
                            experiment_case_key=case_key,
                        )
                    evaluation = run.evaluations[-1]
                    runs.append(run)
                    evaluations.append(evaluation)
                    if evaluation.failure_classification:
                        failures[evaluation.failure_classification] += 1
                    candidate.run_count = len(runs)
                    db.commit()

            count = max(1, len(evaluations))
            candidate.quality = round(mean(item.score for item in evaluations), 4)
            candidate.pass_rate = round(sum(item.passed for item in evaluations) / count, 4)
            candidate.task_completion = round(
                mean(item.task_completion for item in evaluations), 4
            )
            candidate.answer_correctness = round(
                mean(item.answer_correctness for item in evaluations), 4
            )
            candidate.tool_selection_accuracy = round(
                mean(item.tool_selection_accuracy for item in evaluations), 4
            )
            candidate.hallucination_rate = round(
                mean(item.hallucination_rate for item in evaluations), 4
            )
            candidate.latency_p50_ms = _percentile([run.latency_ms for run in runs], 0.50)
            candidate.latency_p95_ms = _percentile([run.latency_ms for run in runs], 0.95)
            candidate.total_cost_usd = round(sum(run.cost_usd for run in runs), 6)
            candidate.cost_per_task = round(candidate.total_cost_usd / count, 6)
            candidate.failure_breakdown = dict(failures)
            previous = _matching_baseline_candidate(baseline, candidate)
            candidate.baseline_quality = previous.quality if previous else None
            candidate.quality_delta = (
                round(candidate.quality - previous.quality, 4)
                if previous and previous.quality is not None and candidate.quality is not None
                else None
            )
            candidate.status = "completed"
            candidate.completed_at = datetime.now(UTC)
            db.commit()

        completed = [item for item in experiment.candidates if item.quality is not None]
        best_quality = max(completed, key=lambda item: item.quality or 0)
        fastest = min(completed, key=lambda item: item.latency_p50_ms or 0)
        cheapest = min(completed, key=lambda item: item.cost_per_task or 0)
        experiment.recommendation = {
            "quality_candidate_id": best_quality.id,
            "fastest_candidate_id": fastest.id,
            "lowest_cost_candidate_id": cheapest.id,
            "summary": (
                f"{best_quality.label} had the highest measured quality on this exact cohort."
            ),
        }
        experiment.status = "completed"
        experiment.completed_at = datetime.now(UTC)
        db.commit()
        db.refresh(experiment)
        return experiment_result(experiment)
    except Exception:
        db.rollback()
        experiment = db.get(Experiment, experiment_id)
        if experiment is not None:
            experiment.status = "failed"
            experiment.completed_at = datetime.now(UTC)
            for candidate in experiment.candidates:
                if candidate.status in {"queued", "running"}:
                    candidate.status = "failed"
            db.commit()
        raise


def experiment_result(experiment: Experiment) -> dict[str, Any]:
    return {
        "experiment_id": experiment.id,
        "dataset_version": experiment.dataset_version,
        "scenario_ids": experiment.scenario_ids,
        "scenario_count": experiment.scenario_count,
        "repetitions": experiment.repetitions,
        "baseline_experiment_id": experiment.baseline_experiment_id,
        "recommendation": experiment.recommendation,
        "candidates": [
            {
                "id": candidate.id,
                "provider": candidate.provider,
                "model": candidate.model,
                "quality": candidate.quality,
                "pass_rate": candidate.pass_rate,
                "run_count": candidate.run_count,
                "quality_delta": candidate.quality_delta,
            }
            for candidate in experiment.candidates
        ],
    }


def experiment_failures(db: Session, experiment_id: str) -> list[dict[str, Any]]:
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise KeyError(experiment_id)
    rows = db.execute(
        select(AgentRun, Evaluation)
        .join(Evaluation, Evaluation.run_id == AgentRun.id)
        .where(
            AgentRun.experiment_candidate_id.in_(
                select(ExperimentCandidate.id).where(
                    ExperimentCandidate.experiment_id == experiment_id
                )
            ),
            Evaluation.evaluator_type == "deterministic+rubric",
            Evaluation.passed.is_(False),
        )
        .order_by(AgentRun.scenario_id, AgentRun.provider)
    ).all()
    labels = {
        item.id: item.label
        for item in db.scalars(
            select(ExperimentCandidate).where(
                ExperimentCandidate.experiment_id == experiment_id
            )
        )
    }
    return [
        {
            "run_id": run.id,
            "candidate_id": run.experiment_candidate_id,
            "candidate_label": labels.get(run.experiment_candidate_id or "", run.model),
            "provider": run.provider,
            "model": run.model,
            "scenario_id": run.scenario_id,
            "category": run.agent_type,
            "task": run.task,
            "output": run.final_output,
            "score": evaluation.score,
            "failure_classification": evaluation.failure_classification,
            "rationale": evaluation.rationale,
            "rubric": evaluation.rubric,
        }
        for run, evaluation in rows
    ]
