from fastapi.testclient import TestClient


def _trace_payload() -> dict:
    return {
        "external_trace_id": "production-trace-001",
        "task": "Answer the customer request from customer@example.com",
        "agent_type": "support",
        "provider": "openai",
        "model": "production-model",
        "status": "completed",
        "final_output": "The request for customer@example.com was answered.",
        "latency_ms": 420,
        "input_tokens": 100,
        "output_tokens": 40,
        "cost_usd": 0.002,
        "spans": [
            {
                "kind": "tool",
                "name": "crm_lookup",
                "duration_ms": 20,
                "input": {"authorization": "Bearer secret", "email": "customer@example.com"},
                "output": {"found": True},
            }
        ],
    }


def test_production_trace_ingestion_is_authenticated_redacted_and_idempotent(
    client: TestClient,
) -> None:
    assert client.post("/api/traces/ingest", json=_trace_payload()).status_code == 401
    headers = {"X-Ingest-Key": "test-ingestion-key-24-chars"}
    first = client.post("/api/traces/ingest", headers=headers, json=_trace_payload())
    second = client.post("/api/traces/ingest", headers=headers, json=_trace_payload())

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert "customer@example.com" not in first.json()["task"]
    assert first.json()["spans"][0]["input"]["authorization"] == "[REDACTED]"
    evaluation = first.json()["evaluations"][0]
    assert evaluation["evaluator_type"] == "online:completion"
    assert evaluation["rubric"]["claims"] == ["completion_only"]


def test_negative_feedback_enters_review_queue_and_can_be_promoted(
    client: TestClient,
) -> None:
    run_id = client.post(
        "/api/traces/ingest",
        headers={"X-Ingest-Key": "test-ingestion-key-24-chars"},
        json={**_trace_payload(), "external_trace_id": "production-trace-002"},
    ).json()["id"]
    feedback = client.post(
        f"/api/runs/{run_id}/feedback",
        json={"rating": -1, "comment": "The answer did not solve the issue."},
    )
    assert feedback.status_code == 201
    queue = client.get("/api/review-queue").json()["items"]
    assert run_id in {item["run_id"] for item in queue}

    pending_review = client.post(
        f"/api/runs/{run_id}/reviews",
        json={
            "reviewer": "Quality operator",
            "verdict": "needs_review",
            "score": 0.5,
            "notes": "A domain expert must decide.",
        },
    )
    assert pending_review.status_code == 201
    queue = client.get("/api/review-queue").json()["items"]
    assert run_id in {item["run_id"] for item in queue}

    promoted = client.post(
        f"/api/runs/{run_id}/promote",
        json={
            "dataset_name": "support-production-failures",
            "expected": {"resolution": "Use the verified recovery procedure."},
            "context": {"ticket": "redacted fixture"},
            "expected_tools": ["knowledge_base_search"],
        },
    )
    assert promoted.status_code == 201
    assert promoted.json()["version"] == 1
    assert promoted.json()["scenario_count"] == 1
    versions = client.get("/api/dataset-versions").json()
    assert versions[0]["status"] == "candidate"

    final_review = client.post(
        f"/api/runs/{run_id}/reviews",
        json={
            "reviewer": "Quality operator",
            "verdict": "fail",
            "score": 0,
            "notes": "Confirmed and promoted to a candidate dataset.",
        },
    )
    assert final_review.status_code == 201
    queue = client.get("/api/review-queue").json()["items"]
    assert run_id not in {item["run_id"] for item in queue}


def test_live_canary_catalog_is_non_blocking(client: TestClient) -> None:
    payload = client.get("/api/canaries").json()
    assert payload["blocking"] is False
