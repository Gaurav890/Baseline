from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AgentRun
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

    assert len(snapshot["recent_runs"]) == 8
    assert Counter(run["agent_type"] for run in snapshot["recent_runs"]) == {
        "research": 2,
        "document": 2,
        "support": 2,
        "extraction": 2,
    }
