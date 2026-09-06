from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
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
