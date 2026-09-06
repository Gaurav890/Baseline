from collections import Counter

from app.evals.catalog import GOLDEN_SCENARIOS, SCENARIO_INDEX


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

