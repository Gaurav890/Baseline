from __future__ import annotations

import hmac
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
from app.evals.workflows import SHOWCASE_SCENARIOS, WORKFLOW_DATASET_VERSION
from app.experiments.service import (
    ExperimentConflictError,
    create_experiment,
    experiment_failures,
)
from app.jobs.queue import get_job_queue
from app.jobs.service import IdempotencyConflictError, create_job
from app.logging_config import configure_logging
from app.models import (
    AgentRun,
    DatasetVersion,
    EvaluationJob,
    Experiment,
    HumanReview,
    SafetyPolicy,
    UserFeedback,
)
from app.providers.registry import provider_registry
from app.schemas import (
    BenchmarkRequest,
    DatasetPromotionCreate,
    EvaluationJobCreate,
    EvaluationJobRead,
    EvaluationJobResult,
    ExperimentCreate,
    ExperimentRead,
    FeedbackCreate,
    HumanReviewCreate,
    IntegrationRead,
    JudgeRequest,
    ProductionTraceCreate,
    RegressionRequest,
    RunCreate,
    RunRead,
    SafetyEventRead,
    SafetyPolicyRead,
    SafetyPolicyUpdate,
)
from app.services.integrations import check_integration, sync_integration_catalog
from app.services.metrics import dashboard_snapshot
from app.services.production_evidence import (
    ingest_trace,
    latest_canaries,
    promote_failure,
    review_queue,
    run_live_canaries,
)
from app.services.safety import (
    ensure_default_safety_policies,
    safety_snapshot,
    update_safety_policy,
)
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
        ensure_default_safety_policies(db)
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


@app.get("/api/safety")
def safety_control_plane(
    db: DbSession, event_limit: int = Query(default=25, ge=1, le=100)
) -> dict[str, Any]:
    snapshot = safety_snapshot(db, event_limit=event_limit)
    snapshot["policies"] = [
        SafetyPolicyRead.model_validate(policy).model_dump(mode="json")
        for policy in snapshot["policies"]
    ]
    snapshot["events"] = [
        SafetyEventRead.model_validate(event).model_dump(mode="json")
        for event in snapshot["events"]
    ]
    return snapshot


@app.patch("/api/safety/policies/{agent_type}", response_model=SafetyPolicyRead)
def change_safety_policy(
    agent_type: str,
    request: SafetyPolicyUpdate,
    db: DbSession,
    x_safety_admin_key: Annotated[str | None, Header(alias="X-Safety-Admin-Key")] = None,
) -> SafetyPolicy:
    if not settings.safety_admin_key:
        raise HTTPException(
            status_code=503, detail="Safety policy administration is not configured"
        )
    if not x_safety_admin_key or not hmac.compare_digest(
        x_safety_admin_key, settings.safety_admin_key
    ):
        raise HTTPException(status_code=401, detail="Invalid safety administration credential")
    if agent_type not in AGENT_PROFILES:
        raise HTTPException(status_code=404, detail="Agent safety policy not found")
    try:
        return update_safety_policy(db, agent_type, request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
                "complexity": profile.complexity,
                "business_value": profile.business_value,
                "example_task": profile.example_task,
                "workflow": [
                    {"name": name, "description": description}
                    for name, description in profile.workflow
                ],
                "output_fields": list(profile.output_fields),
            }
            for profile in AGENT_PROFILES.values()
        ],
        "providers": provider_registry.availability(),
    }


@app.get("/api/workflows")
def workflows() -> dict[str, Any]:
    complex_agent_ids = {scenario.category for scenario in SHOWCASE_SCENARIOS}
    return {
        "dataset_version": WORKFLOW_DATASET_VERSION,
        "blocking": False,
        "release_gate_scenario_count": len(GOLDEN_SCENARIOS),
        "summary": (
            "Complex, multi-step examples for demonstration and exploratory evaluation. "
            "They do not silently change the frozen release gate."
        ),
        "agents": [
            {
                "id": profile.id,
                "name": profile.name,
                "description": profile.description,
                "complexity": profile.complexity,
                "business_value": profile.business_value,
                "example_task": profile.example_task,
                "tools": list(profile.tools),
                "workflow": [
                    {"name": name, "description": description}
                    for name, description in profile.workflow
                ],
                "output_fields": list(profile.output_fields),
                "scenario_count": sum(
                    scenario.category == profile.id for scenario in SHOWCASE_SCENARIOS
                ),
            }
            for profile in AGENT_PROFILES.values()
            if profile.id in complex_agent_ids
        ],
        "scenarios": [
            {
                "id": scenario.id,
                "agent_type": scenario.category,
                "task": scenario.task,
                "expected_tools": list(scenario.expected_tools),
                "output_fields": list(scenario.expected),
                "tags": list(scenario.tags),
                "fixture_kind": scenario.fixture_kind,
                "blocking": scenario.blocking,
                "provenance": sorted(
                    {
                        str(source.get("provenance", "UNVERIFIED")).upper()
                        for source in scenario.context.get("sources", [])
                        if isinstance(source, dict)
                    }
                    | {str(scenario.context.get("incident_provenance", "")).upper()} - {""}
                ),
                "source_urls": [
                    str(source["url"])
                    for source in scenario.context.get("sources", [])
                    if isinstance(source, dict) and source.get("url")
                ],
            }
            for scenario in SHOWCASE_SCENARIOS
        ],
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
                "fixture_kind": scenario.fixture_kind,
                "blocking": scenario.blocking,
                "dataset_version": "golden-v1",
            }
            for scenario in filtered[offset : offset + limit]
        ],
    }


@app.post("/api/runs", response_model=RunRead, status_code=201)
async def create_run(request: RunCreate, db: DbSession) -> AgentRun:
    return await AgentRunner().run(request, db)


@app.get("/api/runs", response_model=list[RunRead])
def list_runs(db: DbSession, limit: int = Query(default=25, ge=1, le=100)) -> list[AgentRun]:
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
def create_human_review(run_id: str, review: HumanReviewCreate, db: DbSession) -> dict[str, Any]:
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
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=255)] = None,
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


@app.post("/api/evaluations/regression", response_model=EvaluationJobRead, status_code=202)
async def regression(
    request: RegressionRequest,
    db: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=255)] = None,
) -> EvaluationJob:
    return await _submit_job(
        EvaluationJobCreate(kind="regression", regression=request), db, idempotency_key
    )


@app.post("/api/benchmarks/compare", response_model=EvaluationJobRead, status_code=202)
async def compare_models(
    request: BenchmarkRequest,
    db: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=255)] = None,
) -> EvaluationJob:
    return await _submit_job(
        EvaluationJobCreate(kind="benchmark", benchmark=request), db, idempotency_key
    )


@app.post("/api/experiments", response_model=ExperimentRead, status_code=202)
async def create_comparison_experiment(
    request: ExperimentCreate,
    db: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=255)] = None,
) -> Experiment:
    try:
        experiment, created = create_experiment(db, request, idempotency_key=idempotency_key)
    except ExperimentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    job = db.get(EvaluationJob, experiment.job_id) if experiment.job_id else None
    if job is None:
        job, _ = create_job(
            db,
            EvaluationJobCreate(
                kind="benchmark",
                benchmark=BenchmarkRequest(
                    scenario_limit=experiment.scenario_count,
                    providers=[candidate.provider for candidate in experiment.candidates],
                ),
                metadata={"experiment_id": experiment.id, "requested_by": "dashboard"},
            ),
            idempotency_key=f"experiment-job:{experiment.id}",
        )
        experiment.job_id = job.id
        db.commit()
    if created or job.status == "queued":
        try:
            await get_job_queue().enqueue(job.id)
        except Exception as exc:
            logger.exception("experiment_enqueue_failed", extra={"experiment_id": experiment.id})
            raise HTTPException(status_code=503, detail="Evaluation queue is unavailable") from exc
    db.expire_all()
    refreshed = db.scalar(
        select(Experiment)
        .options(selectinload(Experiment.candidates))
        .where(Experiment.id == experiment.id)
    )
    assert refreshed is not None
    return refreshed


@app.get("/api/experiments", response_model=list[ExperimentRead])
def list_experiments(
    db: DbSession, limit: int = Query(default=20, ge=1, le=100)
) -> list[Experiment]:
    return list(
        db.scalars(
            select(Experiment)
            .options(selectinload(Experiment.candidates))
            .order_by(desc(Experiment.created_at))
            .limit(limit)
        ).all()
    )


@app.get("/api/experiments/{experiment_id}", response_model=ExperimentRead)
def get_experiment(experiment_id: str, db: DbSession) -> Experiment:
    experiment = db.scalar(
        select(Experiment)
        .options(selectinload(Experiment.candidates))
        .where(Experiment.id == experiment_id)
    )
    if experiment is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return experiment


@app.get("/api/experiments/{experiment_id}/failures")
def get_experiment_failures(experiment_id: str, db: DbSession) -> dict[str, Any]:
    try:
        items = experiment_failures(db, experiment_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Experiment not found") from None
    return {"experiment_id": experiment_id, "total": len(items), "items": items}


@app.post("/api/traces/ingest", response_model=RunRead, status_code=201)
def create_ingested_trace(
    request: ProductionTraceCreate,
    response: Response,
    db: DbSession,
    x_ingest_key: Annotated[str | None, Header(alias="X-Ingest-Key")] = None,
) -> AgentRun:
    if not settings.ingest_api_key:
        raise HTTPException(status_code=503, detail="Production trace ingestion is not configured")
    if not x_ingest_key or not hmac.compare_digest(x_ingest_key, settings.ingest_api_key):
        raise HTTPException(status_code=401, detail="Invalid ingestion credential")
    run, created = ingest_trace(db, request)
    if not created:
        response.status_code = 200
    return run


@app.post("/api/runs/{run_id}/feedback", status_code=201)
def create_user_feedback(run_id: str, feedback: FeedbackCreate, db: DbSession) -> dict[str, Any]:
    if db.get(AgentRun, run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    record = UserFeedback(run_id=run_id, **feedback.model_dump())
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"id": record.id, "run_id": run_id, "rating": record.rating}


@app.get("/api/review-queue")
def get_review_queue(db: DbSession, limit: int = Query(default=25, ge=1, le=100)) -> dict[str, Any]:
    items = review_queue(db, limit)
    return {"total": len(items), "items": items}


@app.post("/api/runs/{run_id}/promote", status_code=201)
def promote_run_to_dataset(
    run_id: str, request: DatasetPromotionCreate, db: DbSession
) -> dict[str, Any]:
    run = db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    version = promote_failure(db, run, request)
    return {
        "id": version.id,
        "dataset_name": version.dataset_name,
        "version": version.version,
        "status": version.status,
        "scenario_count": len(version.scenarios),
        "source_run_id": version.source_run_id,
    }


@app.get("/api/dataset-versions")
def list_dataset_versions(db: DbSession) -> list[dict[str, Any]]:
    versions = db.scalars(
        select(DatasetVersion).order_by(desc(DatasetVersion.created_at)).limit(100)
    ).all()
    return [
        {
            "id": version.id,
            "dataset_name": version.dataset_name,
            "version": version.version,
            "status": version.status,
            "scenario_count": len(version.scenarios),
            "source_run_id": version.source_run_id,
            "created_at": version.created_at.isoformat(),
        }
        for version in versions
    ]


def _canary_payload(records: list[Any]) -> dict[str, Any]:
    return {
        "blocking": False,
        "items": [
            {
                "id": record.id,
                "canary_id": record.canary_id,
                "connector": record.connector,
                "status": record.status,
                "provenance": record.provenance,
                "source_url": record.source_url,
                "latency_ms": record.latency_ms,
                "content_hash": record.content_hash,
                "error": record.error,
                "checked_at": record.checked_at.isoformat(),
            }
            for record in records
        ],
    }


@app.get("/api/canaries")
def get_canaries(db: DbSession) -> dict[str, Any]:
    return _canary_payload(latest_canaries(db))


@app.post("/api/canaries/run", status_code=201)
async def execute_canaries(db: DbSession) -> dict[str, Any]:
    return _canary_payload(await run_live_canaries(db))


@app.post("/api/webhooks/github", status_code=202)
async def github_webhook(
    request: Request,
    db: DbSession,
    x_hub_signature_256: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
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
    if not repository_allowed(pull_request.repository, settings.github_repository_allowlist):
        raise HTTPException(status_code=403, detail="Repository is not allowlisted")

    job = await _submit_job(
        EvaluationJobCreate(
            kind="pr_evaluation",
            regression=RegressionRequest(
                provider=settings.github_evaluation_provider,
                model=settings.github_evaluation_model,
                limit=settings.github_evaluation_limit,
            ),
            metadata=pull_request.metadata(),
        ),
        db,
        f"github:{x_github_delivery}",
    )
    return {"accepted": True, "event": "pull_request", "job": {"id": job.id, "status": job.status}}
