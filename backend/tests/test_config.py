from __future__ import annotations

import pytest

from app.config import Settings


def _production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "demo_mode": False,
        "fixture_connectors": False,
        "database_url": "postgresql://baseline:secret@db.internal/baseline",
        "frontend_origin": "https://baseline.example",
        "job_backend": "redis",
        "redis_url": "redis://redis.internal:6379/0",
        "sec_user_agent": "Baseline Reliability ops@example.com",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_safe_production_configuration_is_accepted() -> None:
    _production_settings().validate()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"demo_mode": True}, "DEMO_MODE must be disabled"),
        ({"fixture_connectors": True}, "BASELINE_FIXTURE_CONNECTORS must be disabled"),
        ({"database_url": "sqlite:///baseline.db"}, "must use PostgreSQL"),
        ({"job_backend": "inline"}, "JOB_BACKEND must be 'redis'"),
        ({"frontend_origin": "http://baseline.example"}, "must use HTTPS"),
        ({"sec_user_agent": None}, "SEC_USER_AGENT must identify"),
    ],
)
def test_production_configuration_rejects_unsafe_values(
    override: dict[str, object], message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        _production_settings(**override).validate()


def test_logging_level_is_validated() -> None:
    with pytest.raises(RuntimeError, match="LOG_LEVEL"):
        Settings(log_level="VERBOSE").validate()
