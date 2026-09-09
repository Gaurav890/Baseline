from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.evals.suite import benchmark_providers, run_regression_suite
from app.models import EvaluationJob
from app.schemas import BenchmarkRequest, EvaluationJobCreate, RegressionRequest

logger = logging.getLogger(__name__)


class IdempotencyConflictError(ValueError):
    """An idempotency key was reused for a different request."""


def _canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def request_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_payload(payload).encode()).hexdigest()


def create_job(
    db: Session,
    request: EvaluationJobCreate,
    *,
    idempotency_key: str | None,
) -> tuple[EvaluationJob, bool]:
    payload = request.job_payload()
    digest = request_hash(payload)
    # Headerless clients get a bounded key so network retries are safe while
    # the same evaluation can still be intentionally rerun in a later window.
    key = idempotency_key or f"implicit:{datetime.now(UTC):%Y%m%d%H}:{digest}"
    existing = db.scalar(select(EvaluationJob).where(EvaluationJob.idempotency_key == key))
    if existing is not None:
        if existing.request_hash != digest:
            raise IdempotencyConflictError(
                "Idempotency-Key has already been used for a different request"
            )
        return existing, False

    job = EvaluationJob(
        idempotency_key=key,
        request_hash=digest,
        kind=request.kind,
        request=payload,
        status="queued",
        queue_backend=settings.job_backend,
        max_attempts=settings.job_max_attempts,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        # Another request can win the unique-key race. Return it only if the
        # canonical request is identical.
        db.rollback()
        existing = db.scalar(select(EvaluationJob).where(EvaluationJob.idempotency_key == key))
        if existing is None or existing.request_hash != digest:
            raise IdempotencyConflictError(
                "Idempotency-Key has already been used for a different request"
            ) from None
        return existing, False
    db.refresh(job)
    return job, True


def _suite_result(suite: Any) -> dict[str, Any]:
    return {
        "id": suite.id,
        "scenario_count": suite.scenario_count,
        "success_rate": suite.success_rate,
        "previous_success_rate": suite.previous_success_rate,
        "baseline_source": suite.baseline_source,
        "regression_detected": suite.regression_detected,
        "failure_breakdown": suite.failure_breakdown,
        "total_cost_usd": suite.total_cost_usd,
        "p95_latency_ms": suite.p95_latency_ms,
    }


async def _renew_lease(job_id: str, worker_id: str) -> None:
    interval = max(10, settings.job_lease_seconds // 3)
    while True:
        await asyncio.sleep(interval)
        with SessionLocal() as lease_db:
            lease_db.execute(
                update(EvaluationJob)
                .where(
                    EvaluationJob.id == job_id,
                    EvaluationJob.status == "running",
                    EvaluationJob.lease_owner == worker_id,
                )
                .values(
                    lease_expires_at=datetime.now(UTC)
                    + timedelta(seconds=settings.job_lease_seconds)
                )
            )
            lease_db.commit()


async def execute_job(
    job_id: str, db: Session, *, worker_id: str = "inline"
) -> EvaluationJob | None:
    job = db.get(EvaluationJob, job_id)
    if job is None:
        logger.warning("evaluation_job_missing", extra={"job_id": job_id})
        return None
    now = datetime.now(UTC)
    claimed = db.execute(
        update(EvaluationJob)
        .where(
            EvaluationJob.id == job_id,
            EvaluationJob.attempts < EvaluationJob.max_attempts,
            or_(
                EvaluationJob.status == "queued",
                (EvaluationJob.status == "running")
                & (EvaluationJob.lease_expires_at < now),
            ),
        )
        .values(
            status="running",
            started_at=now,
            attempts=EvaluationJob.attempts + 1,
            lease_owner=worker_id,
            lease_expires_at=now + timedelta(seconds=settings.job_lease_seconds),
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if claimed.rowcount != 1:
        db.expire_all()
        return db.get(EvaluationJob, job_id)
    db.expire_all()
    job = db.get(EvaluationJob, job_id)
    assert job is not None
    logger.info(
        "evaluation_job_started",
        extra={"job_id": job.id, "job_kind": job.kind, "attempt": job.attempts},
    )

    heartbeat = asyncio.create_task(_renew_lease(job_id, worker_id))
    try:
        parameters = job.request.get("parameters", {})
        if job.kind in {"regression", "pr_evaluation"}:
            suite = await run_regression_suite(RegressionRequest.model_validate(parameters), db)
            result = _suite_result(suite)
            if job.kind == "pr_evaluation":
                metadata = job.request.get("metadata", {})
                result["pull_request"] = metadata
                from app.webhooks.github import publish_check_run

                try:
                    result["github_check"] = await publish_check_run(
                        repository=str(metadata["repository"]),
                        head_sha=str(metadata["head_sha"]),
                        installation_id=(
                            int(metadata["installation_id"])
                            if metadata.get("installation_id") is not None
                            else None
                        ),
                        job_id=job.id,
                        success_rate=suite.success_rate,
                        regression_detected=suite.regression_detected,
                    )
                except Exception as exc:
                    logger.exception("github_check_publish_failed", extra={"job_id": job.id})
                    result["github_check"] = {"published": False, "error": str(exc)[:1000]}
        elif job.kind == "benchmark":
            metadata = job.request.get("metadata", {})
            experiment_id = metadata.get("experiment_id")
            if experiment_id:
                from app.experiments.service import execute_experiment

                result = await execute_experiment(str(experiment_id), db)
            else:
                request = BenchmarkRequest.model_validate(parameters)
                comparison = await benchmark_providers(
                    providers=request.providers,
                    scenario_limit=request.scenario_limit,
                    db=db,
                )
                result = {"scenario_count": request.scenario_limit, "results": comparison}
        else:
            raise ValueError(f"Unsupported evaluation job kind: {job.kind}")

        job = db.get(EvaluationJob, job_id)
        if job is None:
            return None
        job.status = "completed"
        job.result = result
        job.error = None
        job.completed_at = datetime.now(UTC)
        job.lease_owner = None
        job.lease_expires_at = None
        db.commit()
        db.refresh(job)
        logger.info("evaluation_job_completed", extra={"job_id": job.id, "job_kind": job.kind})
    except Exception as exc:
        db.rollback()
        job = db.get(EvaluationJob, job_id)
        if job is None:
            raise
        retry = job.attempts < job.max_attempts
        job.status = "queued" if retry else "failed"
        job.error = str(exc)[:5000]
        job.completed_at = None if retry else datetime.now(UTC)
        job.lease_owner = None
        job.lease_expires_at = None
        db.commit()
        logger.exception("evaluation_job_failed", extra={"job_id": job.id, "job_kind": job.kind})
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
    return job
