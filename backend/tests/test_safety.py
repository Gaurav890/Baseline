from fastapi.testclient import TestClient

from app.config import settings

ADMIN_HEADERS = {"X-Safety-Admin-Key": "test-safety-admin-key-24-chars"}


def test_safety_posture_is_honest_and_policies_are_persisted(client: TestClient) -> None:
    response = client.get("/api/safety")
    assert response.status_code == 200
    payload = response.json()

    assert payload["execution_enabled"] is True
    assert payload["summary"]["policy_count"] == 7
    assert {policy["agent_type"] for policy in payload["policies"]} == {
        "research",
        "document",
        "support",
        "extraction",
        "due_diligence",
        "release_risk",
        "incident_response",
    }
    checks = {check["id"]: check["status"] for check in payload["checks"]}
    assert checks["unexpected-tool"] == "enforced"
    assert checks["sandbox-escape"] == "not_implemented"
    assert payload["containment"]["isolated_execution_sandbox"] is False


def test_safety_policy_update_requires_admin_key_and_blocks_out_of_scope_plan(
    client: TestClient,
) -> None:
    denied = client.patch(
        "/api/safety/policies/research",
        json={"allowed_tools": []},
    )
    assert denied.status_code == 401

    narrowed = client.patch(
        "/api/safety/policies/research",
        headers=ADMIN_HEADERS,
        json={"allowed_tools": []},
    )
    assert narrowed.status_code == 200
    assert narrowed.json()["version"] == 2

    run = client.post(
        "/api/runs",
        json={"task": "Research the supplied evidence", "agent_type": "research"},
    )
    assert run.status_code == 201
    assert run.json()["status"] == "failed"
    assert run.json()["evaluations"][0]["failure_classification"] == "policy_violation"

    events = client.get("/api/safety").json()["events"]
    blocked = next(event for event in events if event["run_id"] == run.json()["id"])
    assert blocked["category"] == "scope"
    assert blocked["decision"] == "blocked"
    assert blocked["input_hash"]
    assert "Research the supplied evidence" not in str(blocked)

    restored = client.patch(
        "/api/safety/policies/research",
        headers=ADMIN_HEADERS,
        json={"allowed_tools": ["web_search", "document_search"]},
    )
    assert restored.status_code == 200


def test_high_risk_support_work_records_approval_without_external_mutation(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/runs",
        json={
            "task": "Review a $750 refund after an outage",
            "agent_type": "support",
            "context": {
                "risk": "high",
                "amount": 750,
                "ticket_snapshot": {"ticket_id": "TKT-SAFE", "status": "open"},
                "knowledge_snapshot": {"resolution": "Escalate for approval"},
            },
        },
    )
    assert response.status_code == 201
    assert response.json()["escalated"] is True

    events = client.get("/api/safety").json()["events"]
    approval = next(
        event
        for event in events
        if event["run_id"] == response.json()["id"] and event["decision"] == "approval_required"
    )
    assert approval["category"] == "authority"
    assert approval["metadata"]["external_mutation_executed"] is False


def test_environment_kill_switch_blocks_new_agent_execution(client: TestClient) -> None:
    previous = settings.agent_execution_enabled
    object.__setattr__(settings, "agent_execution_enabled", False)
    try:
        response = client.post(
            "/api/runs",
            json={"task": "Extract an email", "agent_type": "extraction"},
        )
    finally:
        object.__setattr__(settings, "agent_execution_enabled", previous)

    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert response.json()["evaluations"][0]["failure_classification"] == "policy_violation"
    events = client.get("/api/safety").json()["events"]
    stopped = next(event for event in events if event["run_id"] == response.json()["id"])
    assert stopped["category"] == "containment"
    assert stopped["action"] == "start_run"
