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
        matched = sum(
            _value_similarity(actual.get(key), value) for key, value in expected.items()
        )
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
    correctness = _value_similarity(parsed, scenario.expected) if scenario else completion
    tool_accuracy = _tool_accuracy(actual_tools, scenario.expected_tools) if scenario else 1.0
    hallucination = _hallucination_rate(output, scenario)
    score = round(
        0.30 * completion
        + 0.40 * correctness
        + 0.20 * tool_accuracy
        + 0.10 * (1 - hallucination),
        4,
    )

    failure: str | None = None
    if execution_error:
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
            "answer_correctness": {"weight": 0.40, "score": round(correctness, 4)},
            "tool_selection": {"weight": 0.20, "score": round(tool_accuracy, 4)},
            "groundedness": {"weight": 0.10, "score": round(1 - hallucination, 4)},
        },
        rationale=(
            "All deterministic and rubric checks passed."
            if failure is None
            else f"Primary failure classified as {failure.replace('_', ' ')}."
        ),
    )
