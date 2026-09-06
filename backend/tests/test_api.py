from fastapi.testclient import TestClient

from app.evals.catalog import GOLDEN_SCENARIOS


def test_health_and_dashboard(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    payload = response.json()
    assert payload["evaluation"]["scenario_count"] == 218
    assert {row["provider"] for row in payload["model_comparison"]} <= {"mock"}


def test_list_agents_and_providers(client: TestClient) -> None:
    payload = client.get("/api/agents").json()
    assert {agent["id"] for agent in payload["agents"]} == {
        "research",
        "document",
        "support",
        "extraction",
    }
    assert {provider["provider"] for provider in payload["providers"]} == {
        "mock",
        "openai",
        "anthropic",
        "gemini",
    }


def test_run_captures_full_trace_and_evaluation(client: TestClient) -> None:
    scenario = GOLDEN_SCENARIOS[0]
    response = client.post(
        "/api/runs",
        json={
            "task": scenario.task,
            "agent_type": scenario.category,
            "provider": "mock",
            "context": scenario.context,
            "scenario_id": scenario.id,
        },
    )
    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "completed"
    assert run["evaluations"][0]["passed"] is True
    assert {span["kind"] for span in run["spans"]} == {
        "agent",
        "planner",
        "tool",
        "model",
        "evaluator",
    }
    assert run["planner_output"]["steps"][0]["tool"] == "web_search"


def test_human_review_round_trip(client: TestClient) -> None:
    runs = client.get("/api/runs?limit=1").json()
    response = client.post(
        f"/api/runs/{runs[0]['id']}/reviews",
        json={"reviewer": "qa@example.com", "verdict": "pass", "score": 0.95, "notes": "Grounded."},
    )
    assert response.status_code == 201
    assert response.json()["verdict"] == "pass"


def test_offline_llm_judge_records_a_second_evaluation(client: TestClient) -> None:
    scenario = GOLDEN_SCENARIOS[2]
    run = client.post(
        "/api/runs",
        json={
            "task": scenario.task,
            "agent_type": scenario.category,
            "provider": "mock",
            "context": scenario.context,
            "scenario_id": scenario.id,
        },
    ).json()
    response = client.post(f"/api/runs/{run['id']}/judge", json={"provider": "mock"})
    assert response.status_code == 201
    judgment = response.json()
    assert judgment["evaluator_type"].startswith("llm_judge:mock")
    assert judgment["passed"] is True
    assert judgment["score"] == 1
