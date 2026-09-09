from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DB = Path(f"/tmp/baseline-agent-lab-tests-{os.getpid()}.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["ENVIRONMENT"] = "test"
os.environ["BASELINE_FIXTURE_CONNECTORS"] = "true"
os.environ["GITHUB_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["GITHUB_REPOSITORY_ALLOWLIST"] = "example/baseline"
os.environ["GITHUB_EVALUATION_LIMIT"] = "1"
os.environ["GITHUB_EVALUATION_PROVIDER"] = "mock"
os.environ["INGEST_API_KEY"] = "test-ingestion-key-24-chars"
os.environ["ONLINE_EVAL_SAMPLE_RATE"] = "1"
os.environ["SAFETY_ADMIN_KEY"] = "test-safety-admin-key-24-chars"

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    TEST_DB.unlink(missing_ok=True)


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
