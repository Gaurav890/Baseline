from collections import Counter

from app.evals.catalog import GOLDEN_SCENARIOS, LIVE_CANARIES, SCENARIO_INDEX
from app.evals.workflows import SHOWCASE_SCENARIO_INDEX, SHOWCASE_SCENARIOS


def test_catalog_contains_exactly_218_unique_scenarios() -> None:
    assert len(GOLDEN_SCENARIOS) == 218
    assert len(SCENARIO_INDEX) == 218
    assert len({scenario.id for scenario in GOLDEN_SCENARIOS}) == 218


def test_catalog_covers_all_agent_types() -> None:
    counts = Counter(scenario.category for scenario in GOLDEN_SCENARIOS)
    assert counts == {"research": 55, "document": 55, "support": 54, "extraction": 54}


def test_each_scenario_has_an_oracle_and_expected_tools() -> None:
    for scenario in GOLDEN_SCENARIOS:
        assert scenario.task
        assert scenario.expected
        assert scenario.expected_tools
        assert scenario.tags


def test_public_snapshots_are_versioned_and_blocking() -> None:
    snapshots = [
        scenario for scenario in GOLDEN_SCENARIOS if scenario.fixture_kind == "frozen_snapshot"
    ]
    assert len(snapshots) == 20
    for scenario in snapshots:
        assert scenario.blocking is True
        assert "frozen-regression" in scenario.tags
        source = scenario.context["sources"][0]
        assert source["provenance"] == "SNAPSHOT"
        assert source["snapshot_version"] == "public-sources-v1"
        assert len(source["content_hash"]) == 64
        assert source["url"].startswith(("https://data.sec.gov/", "https://api.github.com/"))


def test_live_canaries_are_not_part_of_blocking_catalog() -> None:
    golden_ids = {scenario.id for scenario in GOLDEN_SCENARIOS}
    assert LIVE_CANARIES
    assert all(canary["blocking"] is False for canary in LIVE_CANARIES)
    assert all(canary["provenance"] == "LIVE" for canary in LIVE_CANARIES)
    assert golden_ids.isdisjoint(canary["id"] for canary in LIVE_CANARIES)


def test_complex_workflows_are_versioned_non_blocking_and_use_real_snapshots() -> None:
    golden_ids = {scenario.id for scenario in GOLDEN_SCENARIOS}
    assert len(SHOWCASE_SCENARIOS) == 6
    assert len(SHOWCASE_SCENARIO_INDEX) == 6
    assert golden_ids.isdisjoint(SHOWCASE_SCENARIO_INDEX)
    assert {scenario.category for scenario in SHOWCASE_SCENARIOS} == {
        "due_diligence",
        "release_risk",
        "incident_response",
    }
    for scenario in SHOWCASE_SCENARIOS:
        assert scenario.blocking is False
        assert scenario.fixture_kind == "mixed_snapshot"
        assert len(scenario.expected_tools) == 4
        assert any(
            source.get("provenance") == "SNAPSHOT"
            and source.get("url", "").startswith(
                ("https://data.sec.gov/", "https://api.github.com/")
            )
            for source in scenario.context["sources"]
        )
