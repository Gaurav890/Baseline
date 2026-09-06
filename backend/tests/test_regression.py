from fastapi.testclient import TestClient


def test_full_218_scenario_regression_suite(client: TestClient) -> None:
    response = client.post(
        "/api/evaluations/regression",
        json={"provider": "mock", "limit": 218, "baseline_success_rate": 0.90, "threshold": 0.01},
    )
    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "completed"
    result = client.get(f"/api/evaluation-jobs/{job['id']}/results")
    assert result.status_code == 200
    suite = result.json()["result"]
    assert suite["scenario_count"] == 218
    assert suite["success_rate"] == 1
    assert suite["regression_detected"] is False
