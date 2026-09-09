from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.evals.catalog import GoldenScenario


@dataclass(frozen=True)
class EvaluationResult:
    evaluator_type: str
    score: float
    passed: bool
    task_completion: float
    answer_correctness: float
    tool_selection_accuracy: float
    hallucination_rate: float
    failure_classification: str | None
    rubric: dict[str, Any]
    rationale: str


def _parse_output(output: str) -> Any:
    cleaned = output.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return cleaned


def _value_similarity(actual: Any, expected: Any) -> float:
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or not expected:
            return 0.0
        matched = sum(_value_similarity(actual.get(key), value) for key, value in expected.items())
        return matched / len(expected)
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return 0.0
        if not expected:
            return 1.0 if not actual else 0.0
        expected_values = {json.dumps(item, sort_keys=True) for item in expected}
        actual_values = {json.dumps(item, sort_keys=True) for item in actual}
        return len(expected_values & actual_values) / len(expected_values | actual_values)
    if expected is None:
        return 1.0 if actual is None else 0.0
    if isinstance(expected, str) and isinstance(actual, str):
        expected_words = set(re.findall(r"\w+", expected.lower()))
        actual_words = set(re.findall(r"\w+", actual.lower()))
        return len(expected_words & actual_words) / max(1, len(expected_words | actual_words))
    return 1.0 if actual == expected else 0.0


_MISSING = object()


def _find_key(value: Any, expected_key: str) -> Any:
    """Find a field in common model response wrappers without changing the answer key."""
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower().replace("_", "") == expected_key.lower().replace("_", ""):
                return nested
        for nested in value.values():
            found = _find_key(nested, expected_key)
            if found is not _MISSING:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_key(nested, expected_key)
            if found is not _MISSING:
                return found
    return _MISSING


def _flatten_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [item for nested in value.values() for item in _flatten_values(nested)]
    if isinstance(value, list):
        return [value, *[item for nested in value for item in _flatten_values(nested)]]
    return [value]


def _field_correctness(parsed: Any, expected: dict[str, Any], *, open_ended: bool) -> float:
    if not expected:
        return 1.0
    flattened = _flatten_values(parsed)
    serialized = json.dumps(parsed, sort_keys=True) if not isinstance(parsed, str) else parsed
    scores: list[float] = []
    for key, expected_value in expected.items():
        actual_value = _find_key(parsed, key)
        if actual_value is not _MISSING:
            scores.append(_value_similarity(actual_value, expected_value))
            continue
        if expected_value is None:
            scores.append(1.0)
            continue
        if open_ended:
            alternatives = [
                _value_similarity(actual, expected_value) for actual in [*flattened, serialized]
            ]
            scores.append(max(alternatives, default=0.0))
        else:
            scores.append(0.0)
    return sum(scores) / len(scores)


def _answer_correctness(parsed: Any, scenario: GoldenScenario) -> tuple[float, str]:
    if scenario.category == "extraction":
        return _value_similarity(parsed, scenario.expected), "strict structured extraction"
    if scenario.category == "research":
        return (
            _field_correctness(parsed, scenario.expected, open_ended=True),
            "evidence field and semantic coverage",
        )
    if scenario.category == "document":
        return (
            _field_correctness(parsed, scenario.expected, open_ended=True),
            "document fact and summary coverage",
        )
    if scenario.category == "support":
        return (
            _field_correctness(parsed, scenario.expected, open_ended=False),
            "ticket resolution and escalation fields",
        )
    return _value_similarity(parsed, scenario.expected), "generic value match"


def _tool_accuracy(actual: list[str], expected: tuple[str, ...]) -> float:
    actual_set, expected_set = set(actual), set(expected)
    if not actual_set and not expected_set:
        return 1.0
    return len(actual_set & expected_set) / max(1, len(actual_set | expected_set))


def _hallucination_rate(output: str, scenario: GoldenScenario | None) -> float:
    urls = set(re.findall(r"""https?://[^\s\]\[\)\}"']+""", output))
    if not urls:
        return 0.0
    if scenario is None:
        return 0.0
    supported = {
        source.get("url")
        for source in scenario.context.get("sources", [])
        if isinstance(source, dict) and source.get("url")
    }
    return len(urls - supported) / len(urls)


def evaluate_run(
    *,
    output: str,
    actual_tools: list[str],
    scenario: GoldenScenario | None,
    execution_error: str | None = None,
) -> EvaluationResult:
    completion = 0.0 if execution_error or len(output.strip()) < 3 else 1.0
    parsed = _parse_output(output)
    if scenario:
        correctness, correctness_method = _answer_correctness(parsed, scenario)
    else:
        correctness, correctness_method = completion, "task completion fallback"
    tool_accuracy = _tool_accuracy(actual_tools, scenario.expected_tools) if scenario else 1.0
    hallucination = _hallucination_rate(output, scenario)
    score = round(
        0.30 * completion + 0.40 * correctness + 0.20 * tool_accuracy + 0.10 * (1 - hallucination),
        4,
    )

    failure: str | None = None
    if execution_error and execution_error.startswith("safety_policy:"):
        failure = "policy_violation"
    elif execution_error:
        failure = "execution_error"
    elif completion < 1:
        failure = "incomplete"
    elif tool_accuracy < 0.8:
        failure = "tool_selection"
    elif hallucination > 0.2:
        failure = "hallucination"
    elif correctness < 0.75:
        failure = "incorrect_answer"

    return EvaluationResult(
        evaluator_type="deterministic+rubric",
        score=score,
        passed=score >= 0.85 and failure is None,
        task_completion=completion,
        answer_correctness=round(correctness, 4),
        tool_selection_accuracy=round(tool_accuracy, 4),
        hallucination_rate=round(hallucination, 4),
        failure_classification=failure,
        rubric={
            "task_completion": {"weight": 0.30, "score": completion},
            "answer_correctness": {
                "weight": 0.40,
                "score": round(correctness, 4),
                "method": correctness_method,
            },
            "tool_selection": {"weight": 0.20, "score": round(tool_accuracy, 4)},
            "groundedness": {"weight": 0.10, "score": round(1 - hallucination, 4)},
        },
        rationale=(
            "All deterministic and rubric checks passed."
            if failure is None
            else f"Primary failure classified as {failure.replace('_', ' ')}."
        ),
    )
