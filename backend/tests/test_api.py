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


def test_golden_scenarios_are_paginated_and_do_not_expose_answer_keys(client: TestClient) -> None:
    response = client.get(
        "/api/scenarios",
        params={"category": "research", "limit": 3, "offset": 0},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 55
    assert len(payload["items"]) == 3
    assert set(payload["items"][0]) == {
        "id",
        "category",
        "task",
        "expected_tools",
        "tags",
        "fixture_kind",
        "blocking",
        "dataset_version",
    }
    assert payload["items"][0]["category"] == "research"


def test_persisted_experiment_compares_the_exact_same_cohort(client: TestClient) -> None:
    payload = {
        "name": "CI model comparison",
        "scenario_limit": 4,
        "candidates": [
            {"provider": "mock", "model": "fixture-model-a", "label": "Candidate A"},
            {"provider": "mock", "model": "fixture-model-b", "label": "Candidate B"},
        ],
    }
    first = client.post(
        "/api/experiments",
        headers={"Idempotency-Key": "experiment-ci-1"},
        json=payload,
    )
    second = client.post(
        "/api/experiments",
        headers={"Idempotency-Key": "experiment-ci-1"},
        json=payload,
    )

    assert first.status_code == 202
    assert second.status_code == 202
    experiment = first.json()
    assert experiment["id"] == second.json()["id"]
    assert experiment["status"] == "completed"
    assert experiment["scenario_ids"] == [
        "research-001",
        "document-001",
        "support-001",
        "extraction-001",
    ]
    assert {candidate["run_count"] for candidate in experiment["candidates"]} == {4}
    assert {candidate["quality"] for candidate in experiment["candidates"]} == {1}
    assert client.get(f"/api/experiments/{experiment['id']}/failures").json()["total"] == 0

    runs = client.get("/api/runs?limit=20").json()
    experiment_runs = [run for run in runs if run["experiment_candidate_id"]]
    by_candidate = {}
    for run in experiment_runs:
        by_candidate.setdefault(run["experiment_candidate_id"], set()).add(run["scenario_id"])
    assert len(by_candidate) == 2
    assert len({tuple(sorted(ids)) for ids in by_candidate.values()}) == 1


def test_experiment_uses_matching_previous_experiment_as_baseline(
    client: TestClient,
) -> None:
    payload = {
        "name": "Repeated CI comparison",
        "scenario_limit": 4,
        "candidates": [
            {"provider": "mock", "model": "fixture-model-a"},
            {"provider": "mock", "model": "fixture-model-b"},
        ],
    }
    response = client.post(
        "/api/experiments",
        headers={"Idempotency-Key": "experiment-ci-2"},
        json=payload,
    )
    assert response.status_code == 202
    experiment = response.json()
    assert experiment["baseline_experiment_id"] is not None
    assert {candidate["baseline_quality"] for candidate in experiment["candidates"]} == {1}
    assert {candidate["quality_delta"] for candidate in experiment["candidates"]} == {0}


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
