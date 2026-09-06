from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.connectors.base import ConnectorHealth, HealthStatus, Provenance
from app.database import SessionLocal
from app.services.integrations import _persist_snapshot


def test_liveness_and_readiness(client: TestClient) -> None:
    assert client.get("/health/live").json()["status"] == "ok"
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {"database": "ok", "job_queue": "ok"}


def test_integration_catalog_does_not_claim_unverified_sources_are_live(
    client: TestClient,
) -> None:
    response = client.get("/api/integrations")
    assert response.status_code == 200
    integrations = {item["name"]: item for item in response.json()["items"]}
    assert {"sec_edgar", "tavily", "github", "url_fetch"} <= integrations.keys()
    assert integrations["github"]["configured"] is True
    assert integrations["github"]["health_status"] == "unchecked"
    assert integrations["github"]["live"] is False
    assert integrations["github"]["provenance"] is None


def test_evaluation_jobs_are_persistent_and_idempotent(client: TestClient) -> None:
    payload = {
        "kind": "regression",
        "regression": {"provider": "mock", "limit": 1},
    }
    first = client.post(
        "/api/evaluation-jobs",
        headers={"Idempotency-Key": "regression-commit-abc"},
        json=payload,
    )
    second = client.post(
        "/api/evaluation-jobs",
        headers={"Idempotency-Key": "regression-commit-abc"},
        json=payload,
    )
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["status"] == "completed"
    job_id = second.json()["id"]
    assert client.get(f"/api/evaluation-jobs/{job_id}").json()["attempts"] == 1
    result = client.get(f"/api/evaluation-jobs/{job_id}/results")
    assert result.status_code == 200
    assert result.json()["result"]["scenario_count"] == 1

    conflict = client.post(
        "/api/evaluation-jobs",
        headers={"Idempotency-Key": "regression-commit-abc"},
        json={"kind": "regression", "regression": {"provider": "mock", "limit": 2}},
    )
    assert conflict.status_code == 409


def test_headerless_retries_are_idempotent_within_the_execution_window(
    client: TestClient,
) -> None:
    payload = {
        "kind": "benchmark",
        "benchmark": {"scenario_limit": 1, "providers": ["mock"]},
    }
    first = client.post("/api/evaluation-jobs", json=payload)
    second = client.post("/api/evaluation-jobs", json=payload)
    assert first.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["attempts"] == 1


def test_public_api_cannot_spoof_pr_check_jobs(client: TestClient) -> None:
    response = client.post(
        "/api/evaluation-jobs",
        json={
            "kind": "pr_evaluation",
            "metadata": {
                "repository": "example/baseline",
                "head_sha": "a" * 40,
                "pull_number": 42,
            },
        },
    )
    assert response.status_code == 403


def test_failed_recheck_clears_live_flag_but_preserves_last_success() -> None:
    successful_at = datetime.now(UTC)
    with SessionLocal() as db:
        healthy = ConnectorHealth(
            connector="stale_health_test",
            display_name="Stale health test",
            configured=True,
            status=HealthStatus.HEALTHY,
            provenance=Provenance.LIVE,
            live=True,
            checked_at=successful_at,
            last_successful_sync=successful_at,
        )
        record = _persist_snapshot(db, healthy, verified=True)
        db.commit()
        assert record.live is True

        unhealthy = ConnectorHealth(
            connector="stale_health_test",
            display_name="Stale health test",
            configured=True,
            status=HealthStatus.UNHEALTHY,
            provenance=Provenance.LIVE,
            live=False,
            checked_at=datetime.now(UTC),
            last_successful_sync=successful_at,
            error="upstream unavailable",
        )
        record = _persist_snapshot(db, unhealthy, verified=False)
        db.commit()
        assert record.live is False
        assert record.provenance == "LIVE"
        assert record.last_success_at is not None


def test_github_webhook_rejects_invalid_signature(client: TestClient) -> None:
    response = client.post(
        "/api/webhooks/github",
        headers={
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
            "X-GitHub-Event": "ping",
            "X-GitHub-Delivery": "delivery-invalid",
        },
        content=b"{}",
    )
    assert response.status_code == 401


def test_github_pull_request_delivery_creates_one_job(client: TestClient) -> None:
    payload = {
        "action": "synchronize",
        "number": 42,
        "repository": {"full_name": "example/baseline"},
        "pull_request": {
            "head": {"sha": "a" * 40},
            "html_url": "https://github.com/example/baseline/pull/42",
        },
        "installation": {"id": 123},
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(b"test-webhook-secret", body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": f"sha256={signature}",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery-42",
    }
    first = client.post("/api/webhooks/github", headers=headers, content=body)
    second = client.post("/api/webhooks/github", headers=headers, content=body)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job"]["id"] == second.json()["job"]["id"]
    job = client.get(f"/api/evaluation-jobs/{first.json()['job']['id']}").json()
    assert job["status"] == "completed"
    assert job["request"]["metadata"]["repository"] == "example/baseline"
    assert job["result"]["github_check"] == {
        "published": False,
        "reason": "checks_disabled",
    }
