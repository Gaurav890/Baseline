from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.connectors import connector_registry
from app.evals.catalog import LIVE_CANARIES
from app.models import (
    AgentRun,
    CanaryRun,
    DatasetVersion,
    Evaluation,
    HumanReview,
    TraceSpan,
    UserFeedback,
)
from app.schemas import DatasetPromotionCreate, ProductionTraceCreate

_SECRET_KEYS = re.compile(r"(?:secret|token|password|authorization|api[_-]?key)", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _SECRET_KEYS.search(str(key)) else _redact(nested)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _EMAIL.sub("[REDACTED_EMAIL]", value)
    return value


def _sampled(external_trace_id: str) -> bool:
    bucket = int(hashlib.sha256(external_trace_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return bucket < settings.online_eval_sample_rate


def ingest_trace(db: Session, request: ProductionTraceCreate) -> tuple[AgentRun, bool]:
    existing = db.scalar(
        select(AgentRun).where(AgentRun.external_trace_id == request.external_trace_id)
    )
    if existing is not None:
        return existing, False
    now = datetime.now(UTC)
    run = AgentRun(
        external_trace_id=request.external_trace_id,
        task=_redact(request.task),
        agent_type=request.agent_type,
        provider=request.provider,
        model=request.model,
        status=request.status,
        final_output=_redact(request.final_output),
        latency_ms=request.latency_ms,
        input_tokens=request.input_tokens,
        output_tokens=request.output_tokens,
        cost_usd=request.cost_usd,
        error=_redact(request.error) if request.error else None,
        completed_at=now,
    )
    db.add(run)
    db.flush()
    for span in request.spans:
        db.add(
            TraceSpan(
                run_id=run.id,
                kind=span.kind,
                name=span.name,
                status=span.status,
                started_at=now,
                ended_at=now,
                duration_ms=span.duration_ms,
                input=_redact(span.input),
                output=_redact(span.output),
                attributes={
                    **_redact(span.attributes),
                    "ingested": True,
                    "provenance": "SNAPSHOT",
                },
            )
        )
    if _sampled(request.external_trace_id):
        completion = float(request.status == "completed" and len(request.final_output.strip()) >= 3)
        db.add(
            Evaluation(
                run_id=run.id,
                evaluator_type="online:completion",
                score=completion,
                passed=completion == 1,
                task_completion=completion,
                answer_correctness=0,
                tool_selection_accuracy=0,
                hallucination_rate=0,
                failure_classification=None if completion else "incomplete",
                rubric={
                    "scope": "sampled_online",
                    "claims": ["completion_only"],
                    "sample_rate": settings.online_eval_sample_rate,
                },
                rationale=(
                    "Sampled online completion check passed; correctness was not inferred."
                    if completion
                    else "The sampled production trace did not contain a completed output."
                ),
            )
        )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(AgentRun).where(AgentRun.external_trace_id == request.external_trace_id)
        )
        if existing is None:
            raise
        return existing, False
    db.refresh(run)
    return run, True


def review_queue(db: Session, limit: int) -> list[dict[str, Any]]:
    reviewed = select(HumanReview.run_id).where(HumanReview.verdict.in_(["pass", "fail"]))
    negative_feedback = select(UserFeedback.run_id).where(UserFeedback.rating < 0)
    runs = db.scalars(
        select(AgentRun)
        .where(
            AgentRun.id.not_in(reviewed),
            AgentRun.id.in_(negative_feedback)
            | (AgentRun.status == "failed")
            | AgentRun.evaluations.any(Evaluation.passed.is_(False)),
        )
        .order_by(desc(AgentRun.created_at))
        .limit(limit)
    ).all()
    return [
        {
            "run_id": run.id,
            "task": run.task,
            "agent_type": run.agent_type,
            "provider": run.provider,
            "model": run.model,
            "status": run.status,
            "reason": "failed_run" if run.status == "failed" else "quality_signal",
            "created_at": run.created_at.isoformat(),
        }
        for run in runs
    ]


def promote_failure(
    db: Session, run: AgentRun, request: DatasetPromotionCreate
) -> DatasetVersion:
    previous = db.scalar(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_name == request.dataset_name)
        .order_by(desc(DatasetVersion.version))
        .limit(1)
    )
    prior_scenarios = list(previous.scenarios) if previous else []
    version = DatasetVersion(
        dataset_name=request.dataset_name,
        version=(previous.version + 1) if previous else 1,
        parent_version_id=previous.id if previous else None,
        source_run_id=run.id,
        status="candidate",
        scenarios=[
            *prior_scenarios,
            {
                "id": f"production-{run.id}",
                "task": run.task,
                "category": run.agent_type,
                "context": _redact(request.context),
                "expected": _redact(request.expected),
                "expected_tools": request.expected_tools,
                "tags": sorted(set([*request.tags, "non-blocking-candidate"])),
                "source_run_id": run.id,
            },
        ],
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


async def run_live_canaries(db: Session) -> list[CanaryRun]:
    records: list[CanaryRun] = []
    for canary in LIVE_CANARIES:
        payload = (
            {"cik": "0000320193", "operation": "submissions"}
            if canary["connector"] == "sec_edgar"
            else {
                "owner": "pallets",
                "repo": "flask",
                "operation": "issue",
                "issue_number": 1,
            }
        )
        try:
            result = await asyncio.to_thread(
                connector_registry.get(str(canary["connector"])).execute, payload
            )
            record = CanaryRun(
                canary_id=str(canary["id"]),
                connector=str(canary["connector"]),
                status="passed",
                provenance=result.metadata.provenance.value,
                source_url=result.metadata.source_url,
                latency_ms=result.metadata.latency_ms,
                content_hash=result.metadata.content_hash,
            )
        except Exception as exc:
            record = CanaryRun(
                canary_id=str(canary["id"]),
                connector=str(canary["connector"]),
                status="failed",
                provenance=None,
                source_url=str(canary["source_url"]),
                error=f"{type(exc).__name__}: {exc}"[:2000],
            )
        db.add(record)
        records.append(record)
    db.commit()
    for record in records:
        db.refresh(record)
    return records


def latest_canaries(db: Session) -> list[CanaryRun]:
    records = db.scalars(select(CanaryRun).order_by(desc(CanaryRun.checked_at))).all()
    latest: dict[str, CanaryRun] = {}
    for record in records:
        latest.setdefault(record.canary_id, record)
    return list(latest.values())
