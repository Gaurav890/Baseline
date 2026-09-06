from app.evals.catalog import GOLDEN_SCENARIOS
from app.evals.evaluators import evaluate_run


def test_exact_golden_output_passes() -> None:
    scenario = GOLDEN_SCENARIOS[0]
    result = evaluate_run(
        output=(
            '{"answer":"The evidence identifies vendor concentration risk as a monitored control '
            'area.","confidence":"high","sources":["https://evidence.example/research/1"]}'
        ),
        actual_tools=list(scenario.expected_tools),
        scenario=scenario,
    )
    assert result.passed
    assert result.answer_correctness == 1
    assert result.tool_selection_accuracy == 1
    assert result.hallucination_rate == 0


def test_wrong_tool_is_classified() -> None:
    scenario = GOLDEN_SCENARIOS[1]
    result = evaluate_run(
        output='{"answer":"plausible but used the wrong tool"}',
        actual_tools=["document_search"],
        scenario=scenario,
    )
    assert not result.passed
    assert result.failure_classification == "tool_selection"


def test_unsupported_url_counts_as_hallucination() -> None:
    scenario = GOLDEN_SCENARIOS[0]
    output = '{"sources":["https://invented.example/not-in-source-packet"]}'
    result = evaluate_run(
        output=output,
        actual_tools=list(scenario.expected_tools),
        scenario=scenario,
    )
    assert result.hallucination_rate == 1

