from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AgentRun, Evaluation
from app.services.metrics import dashboard_snapshot


def test_empty_dashboard_does_not_invent_failures_or_runs() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        snapshot = dashboard_snapshot(db)

    assert snapshot["operations"]["runs"] == 0
    assert snapshot["quality"]["failure_rate"] == 0
    assert snapshot["model_comparison"] == []
    assert snapshot["latest_suite"] is None
    assert snapshot["recent_runs"] == []


def test_dashboard_recent_runs_represent_each_agent_type() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    with Session(engine) as db:
        categories = ("research", "document", "support", "extraction")
        for category_index, category in enumerate(categories):
            for run_index in range(3):
                db.add(
                    AgentRun(
                        id=f"{category}-{run_index}",
                        task=f"{category} task {run_index}",
                        agent_type=category,
                        provider="mock",
                        model="mock",
                        status="completed",
                        created_at=now - timedelta(minutes=category_index * 10 + run_index),
                    )
                )
        db.commit()
        snapshot = dashboard_snapshot(db)

    assert len(snapshot["recent_runs"]) == 10
    assert Counter(run["agent_type"] for run in snapshot["recent_runs"]) == {
        "research": 3,
        "document": 3,
        "support": 2,
        "extraction": 2,
    }


def test_showcase_runs_do_not_inflate_golden_coverage() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = AgentRun(
            task="Due diligence",
            agent_type="due_diligence",
            provider="mock",
            model="mock",
            status="completed",
            scenario_id="due-diligence-apple-001",
        )
        run.evaluations.append(
            Evaluation(
                evaluator_type="deterministic+rubric",
                score=1,
                passed=True,
                task_completion=1,
                answer_correctness=1,
                tool_selection_accuracy=1,
                hallucination_rate=0,
                rubric={},
                rationale="Passed",
            )
        )
        db.add(run)
        db.commit()
        snapshot = dashboard_snapshot(db)

    methods = {method["name"]: method for method in snapshot["evaluation"]["methods"]}
    assert methods["Deterministic tests"]["coverage"] == 0
    assert methods["Golden datasets"]["coverage"] == 218
    assert methods["Workflow showcase"]["coverage"] == 1
