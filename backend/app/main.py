from __future__ import annotations

import json
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session, selectinload

from app.agents.profiles import AGENT_PROFILES
from app.agents.runner import AgentRunner
from app.config import settings
from app.database import Base, SessionLocal, engine, get_db
from app.evals.catalog import GOLDEN_SCENARIOS
from app.evals.judge import judge_run
from app.jobs.queue import get_job_queue
from app.jobs.service import IdempotencyConflictError, create_job
from app.logging_config import configure_logging
from app.models import AgentRun, EvaluationJob, HumanReview
from app.providers.registry import provider_registry
from app.schemas import (
    BenchmarkRequest,
    EvaluationJobCreate,
    EvaluationJobRead,
    EvaluationJobResult,
    HumanReviewCreate,
    IntegrationRead,
    JudgeRequest,
    RegressionRequest,
    RunCreate,
    RunRead,
)
from app.services.integrations import check_integration, sync_integration_catalog
from app.services.metrics import dashboard_snapshot
from app.services.seed import seed_demo_data
from app.webhooks.github import parse_pull_request_event, repository_allowed, verify_signature

DbSession = Annotated[Session, Depends(get_db)]
logger = logging.getLogger(__name__)
_DELIVERY_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_MAX_WEBHOOK_BYTES = 1_000_000


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate()
    configure_logging(settings.log_level)
    if not settings.is_production:
        Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        if settings.demo_mode:
            seed_demo_data(db)
        sync_integration_catalog(db)
    yield


app = FastAPI(
    title="Baseline API",
    version="0.1.0",
    description="Evaluation, regression testing, and trace observability for AI agents.",
    lifespan=lifespan,
)
cors_origins = [settings.frontend_origin]
if not settings.is_production:
    cors_origins.extend(["http://localhost:3000", "http://127.0.0.1:3000"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(dict.fromkeys(cors_origins)),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logging(request: Request, call_next: Any) -> Response:
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "http_request_failed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": 500,
                "duration_ms": round((time.perf_counter() - started) * 1000),
            },
        )
        raise
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "http_request",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        },
    )
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "baseline-api"}


@app.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "ok", "service": "baseline-api"}


@app.get("/health/ready")
def readiness(db: DbSession) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        logger.exception("readiness_database_failed")
        checks["database"] = "unavailable"
    try:
        checks["job_queue"] = "ok" if get_job_queue().ready() else "unavailable"
    except Exception:
        logger.exception("readiness_job_queue_failed")
        checks["job_queue"] = "unavailable"
    ready = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )


@app.get("/api/dashboard")
def dashboard(db: DbSession) -> dict[str, Any]:
    return dashboard_snapshot(db)


@app.get("/api/integrations")
def integrations(db: DbSession) -> dict[str, Any]:
    records = sync_integration_catalog(db)
    return {
        "items": [
            IntegrationRead.model_validate(record).model_dump(mode="json") for record in records
        ],
        "generated_at": datetime.now(UTC).isoformat(),
    }


@app.post("/api/integrations/{name}/check", response_model=IntegrationRead)
async def integration_check(name: str, db: DbSession) -> Any:
    try:
        return await check_integration(name, db)
    except KeyError:
        raise HTTPException(status_code=404, detail="Integration not found") from None


@app.get("/api/agents")
def agents() -> dict[str, Any]:
    return {
        "agents": [
            {
                "id": profile.id,
                "name": profile.name,
                "description": profile.description,
                "tools": list(profile.tools),
                "accent": profile.accent,
            }
            for profile in AGENT_PROFILES.values()
        ],
        "providers": provider_registry.availability(),
    }


@app.get("/api/scenarios")
def scenarios(
    category: str | None = None,
    limit: int = Query(default=25, ge=1, le=218),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    filtered = [
        scenario
        for scenario in GOLDEN_SCENARIOS
        if category is None or scenario.category == category
    ]
    return {
        "total": len(filtered),
        "items": [
            {
                "id": scenario.id,
                "category": scenario.category,
                "task": scenario.task,
                "expected_tools": scenario.expected_tools,
                "tags": scenario.tags,
            }
            for scenario in filtered[offset : offset + limit]
        ],
    }


@app.post("/api/runs", response_model=RunRead, status_code=201)
async def create_run(request: RunCreate, db: DbSession) -> AgentRun:
    return await AgentRunner().run(request, db)


@app.get("/api/runs", response_model=list[RunRead])
def list_runs(
    db: DbSession, limit: int = Query(default=25, ge=1, le=100)
) -> list[AgentRun]:
    return list(
        db.scalars(
            select(AgentRun)
            .options(selectinload(AgentRun.spans), selectinload(AgentRun.evaluations))
            .order_by(desc(AgentRun.created_at))
            .limit(limit)
        ).all()
    )


@app.get("/api/runs/{run_id}", response_model=RunRead)
def get_run(run_id: str, db: DbSession) -> AgentRun:
    run = db.scalar(
        select(AgentRun)
        .options(selectinload(AgentRun.spans), selectinload(AgentRun.evaluations))
        .where(AgentRun.id == run_id)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.post("/api/runs/{run_id}/reviews", status_code=201)
def create_human_review(
    run_id: str, review: HumanReviewCreate, db: DbSession
) -> dict[str, Any]:
    if db.get(AgentRun, run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    record = HumanReview(run_id=run_id, **review.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    return {
        "id": record.id,
        "run_id": record.run_id,
        "verdict": record.verdict,
        "score": record.score,
    }


@app.post("/api/runs/{run_id}/judge", status_code=201)
async def create_judge_evaluation(
    run_id: str, request: JudgeRequest, db: DbSession
) -> dict[str, Any]:
    run = db.scalar(
        select(AgentRun)
        .options(selectinload(AgentRun.spans), selectinload(AgentRun.evaluations))
        .where(AgentRun.id == run_id)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    evaluation = await judge_run(
        run=run,
        provider=request.provider,
        model=request.model,
        db=db,
    )
    return {
        "id": evaluation.id,
        "evaluator_type": evaluation.evaluator_type,
        "score": evaluation.score,
        "passed": evaluation.passed,
        "failure_classification": evaluation.failure_classification,
        "rubric": evaluation.rubric,
        "rationale": evaluation.rationale,
    }


async def _submit_job(
    request: EvaluationJobCreate,
    db: Session,
    idempotency_key: str | None,
) -> EvaluationJob:
    try:
        job, created = create_job(db, request, idempotency_key=idempotency_key)
    except IdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if created or job.status == "queued":
        try:
            await get_job_queue().enqueue(job.id)
        except Exception as exc:
            logger.exception("evaluation_job_enqueue_failed", extra={"job_id": job.id})
            raise HTTPException(status_code=503, detail="Evaluation queue is unavailable") from exc
    db.expire_all()
    refreshed = db.get(EvaluationJob, job.id)
    assert refreshed is not None
    return refreshed


@app.post("/api/evaluation-jobs", response_model=EvaluationJobRead, status_code=202)
async def create_evaluation_job(
    request: EvaluationJobCreate,
    db: DbSession,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", max_length=255)
    ] = None,
) -> EvaluationJob:
    if request.kind == "pr_evaluation":
        raise HTTPException(
            status_code=403,
            detail="PR evaluation jobs can only be created by a verified GitHub webhook",
        )
    return await _submit_job(request, db, idempotency_key)


@app.get("/api/evaluation-jobs", response_model=list[EvaluationJobRead])
def list_evaluation_jobs(
    db: DbSession, limit: int = Query(default=25, ge=1, le=100)
) -> list[EvaluationJob]:
    return list(
        db.scalars(
            select(EvaluationJob).order_by(desc(EvaluationJob.created_at)).limit(limit)
        ).all()
    )


@app.get("/api/evaluation-jobs/{job_id}", response_model=EvaluationJobRead)
def get_evaluation_job(job_id: str, db: DbSession) -> EvaluationJob:
    job = db.get(EvaluationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Evaluation job not found")
    return job


@app.get("/api/evaluation-jobs/{job_id}/results", response_model=EvaluationJobResult)
def get_evaluation_job_results(
    job_id: str, response: Response, db: DbSession
) -> EvaluationJobResult:
    job = db.get(EvaluationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Evaluation job not found")
    if job.status == "failed":
        raise HTTPException(status_code=409, detail=job.error or "Evaluation job failed")
    if job.status in {"queued", "running"}:
        response.status_code = 202
    return EvaluationJobResult(
        job_id=job.id,
        status=job.status,
        result=job.result,
        error=job.error,
    )


@app.post(
    "/api/evaluations/regression", response_model=EvaluationJobRead, status_code=202
)
async def regression(
    request: RegressionRequest,
    db: DbSession,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", max_length=255)
    ] = None,
) -> EvaluationJob:
    return await _submit_job(
        EvaluationJobCreate(kind="regression", regression=request), db, idempotency_key
    )


@app.post("/api/benchmarks/compare", response_model=EvaluationJobRead, status_code=202)
async def compare_models(
    request: BenchmarkRequest,
    db: DbSession,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", max_length=255)
    ] = None,
) -> EvaluationJob:
    return await _submit_job(
        EvaluationJobCreate(kind="benchmark", benchmark=request), db, idempotency_key
    )


@app.post("/api/webhooks/github", status_code=202)
async def github_webhook(
    request: Request,
    db: DbSession,
    x_hub_signature_256: Annotated[
        str | None, Header(alias="X-Hub-Signature-256")
    ] = None,
    x_github_event: Annotated[str | None, Header(alias="X-GitHub-Event")] = None,
    x_github_delivery: Annotated[str | None, Header(alias="X-GitHub-Delivery")] = None,
) -> dict[str, Any]:
    if not settings.github_webhook_secret:
        raise HTTPException(status_code=503, detail="GitHub webhooks are not configured")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header") from exc
        if declared_length < 0:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header")
        if declared_length > _MAX_WEBHOOK_BYTES:
            raise HTTPException(status_code=413, detail="Webhook payload is too large")
    body = await request.body()
    if len(body) > _MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Webhook payload is too large")
    if not verify_signature(body, x_hub_signature_256, settings.github_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    if not x_github_delivery or not _DELIVERY_ID.fullmatch(x_github_delivery):
        raise HTTPException(status_code=400, detail="Invalid GitHub delivery id")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Webhook body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook body must be a JSON object")
    if x_github_event == "ping":
        return {"accepted": True, "event": "ping"}
    if x_github_event != "pull_request":
        return {"accepted": False, "event": x_github_event, "reason": "event_ignored"}
    try:
        pull_request = parse_pull_request_event(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if pull_request is None:
        return {"accepted": False, "event": "pull_request", "reason": "action_ignored"}
    if not repository_allowed(
        pull_request.repository, settings.github_repository_allowlist
    ):
        raise HTTPException(status_code=403, detail="Repository is not allowlisted")

    job = await _submit_job(
        EvaluationJobCreate(
            kind="pr_evaluation",
            regression=RegressionRequest(limit=settings.github_evaluation_limit),
            metadata=pull_request.metadata(),
        ),
        db,
        f"github:{x_github_delivery}",
    )
    return {"accepted": True, "event": "pull_request", "job": {"id": job.id, "status": job.status}}
