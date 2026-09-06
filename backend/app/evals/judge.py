from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models import AgentRun, Evaluation
from app.providers.registry import provider_registry

JUDGE_SYSTEM_PROMPT = """You are a strict evaluator of tool-using agent runs.
Score only the evidence in the packet. Return JSON with task_completion,
answer_correctness, tool_selection_accuracy, groundedness (each 0 to 1),
failure_classification (null or a concise snake_case category), and rationale.
Do not reward eloquence when the answer is unsupported."""


def _parse_judgment(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    payload = json.loads(cleaned)
    required = {
        "task_completion",
        "answer_correctness",
        "tool_selection_accuracy",
        "groundedness",
        "rationale",
    }
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise ValueError("Judge returned an incomplete rubric")
    return payload


async def judge_run(
    *, run: AgentRun, provider: str, model: str | None, db: Session
) -> Evaluation:
    reference = run.evaluations[-1] if run.evaluations else None
    packet = {
        "mode": "judge",
        "task": run.task,
        "agent_type": run.agent_type,
        "planner_output": run.planner_output,
        "tool_evidence": [
            {"tool": span.name, "input": span.input, "output": span.output}
            for span in run.spans
            if span.kind == "tool"
        ],
        "final_output": run.final_output,
        "reference_evaluation": (
            {
                "task_completion": reference.task_completion,
                "answer_correctness": reference.answer_correctness,
                "tool_selection_accuracy": reference.tool_selection_accuracy,
                "hallucination_rate": reference.hallucination_rate,
                "failure_classification": reference.failure_classification,
            }
            if reference
            else {}
        ),
    }
    selected_model = model or provider_registry.default_model(provider)
    response = await provider_registry.get(provider).generate(
        system=JUDGE_SYSTEM_PROMPT,
        prompt=json.dumps(packet, sort_keys=True),
        model=selected_model,
    )
    result = _parse_judgment(response.text)
    completion = min(1.0, max(0.0, float(result["task_completion"])))
    correctness = min(1.0, max(0.0, float(result["answer_correctness"])))
    tool_accuracy = min(1.0, max(0.0, float(result["tool_selection_accuracy"])))
    groundedness = min(1.0, max(0.0, float(result["groundedness"])))
    score = round(
        0.30 * completion + 0.40 * correctness + 0.20 * tool_accuracy + 0.10 * groundedness,
        4,
    )
    failure = result.get("failure_classification")
    evaluation = Evaluation(
        run_id=run.id,
        evaluator_type=f"llm_judge:{provider}:{selected_model}",
        score=score,
        passed=score >= 0.85 and not failure,
        task_completion=completion,
        answer_correctness=correctness,
        tool_selection_accuracy=tool_accuracy,
        hallucination_rate=round(1 - groundedness, 4),
        failure_classification=str(failure)[:80] if failure else None,
        rubric={
            "task_completion": {"weight": 0.30, "score": completion},
            "answer_correctness": {"weight": 0.40, "score": correctness},
            "tool_selection": {"weight": 0.20, "score": tool_accuracy},
            "groundedness": {"weight": 0.10, "score": groundedness},
        },
        rationale=str(result["rationale"]),
    )
    db.add(evaluation)
    db.commit()
    db.refresh(evaluation)
    return evaluation
