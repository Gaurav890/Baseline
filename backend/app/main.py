from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

from app.agents.profiles import AGENT_PROFILES
from app.agents.runner import AgentRunner
from app.config import settings
from app.database import Base, SessionLocal, engine, get_db
from app.evals.catalog import GOLDEN_SCENARIOS
from app.evals.judge import judge_run
from app.evals.suite import benchmark_providers, run_regression_suite
from app.models import AgentRun, HumanReview
from app.providers.registry import provider_registry
from app.schemas import (
    BenchmarkRequest,
    HumanReviewCreate,
    JudgeRequest,
    RegressionRequest,
    RunCreate,
    RunRead,
)
from app.services.metrics import dashboard_snapshot
from app.services.seed import seed_demo_data

DbSession = Annotated[Session, Depends(get_db)]


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_demo_data(db)
    yield


app = FastAPI(
    title="Baseline API",
    version="0.1.0",
    description="Evaluation, regression testing, and trace observability for AI agents.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(
        {
            settings.frontend_origin,
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        }
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "baseline-api"}


@app.get("/api/dashboard")
def dashboard(db: DbSession) -> dict[str, Any]:
    return dashboard_snapshot(db)


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


@app.post("/api/evaluations/regression")
async def regression(
    request: RegressionRequest, db: DbSession
) -> dict[str, Any]:
    suite = await run_regression_suite(request, db)
    return {
        "id": suite.id,
        "scenario_count": suite.scenario_count,
        "success_rate": suite.success_rate,
        "previous_success_rate": suite.previous_success_rate,
        "regression_detected": suite.regression_detected,
        "failure_breakdown": suite.failure_breakdown,
        "total_cost_usd": suite.total_cost_usd,
        "p95_latency_ms": suite.p95_latency_ms,
    }


@app.post("/api/benchmarks/compare")
async def compare_models(
    request: BenchmarkRequest, db: DbSession
) -> dict[str, Any]:
    return {
        "scenario_count": request.scenario_limit,
        "results": await benchmark_providers(
            providers=request.providers,
            scenario_limit=request.scenario_limit,
            db=db,
        ),
    }
