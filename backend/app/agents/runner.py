from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.agents.profiles import AGENT_PROFILES, choose_tools
from app.evals.catalog import SCENARIO_INDEX, GoldenScenario
from app.evals.evaluators import evaluate_run
from app.models import AgentRun, Evaluation, TraceSpan
from app.providers.registry import provider_registry
from app.schemas import RunCreate
from app.tools.registry import ToolRegistry, default_tools


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AgentRunner:
    def __init__(self, tools: ToolRegistry = default_tools) -> None:
        self.tools = tools

    @staticmethod
    def _payload_for_tool(
        name: str, task: str, context: dict[str, Any], observations: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if name == "web_search":
            return {"query": task, "sources": context.get("sources", [])}
        if name == "document_search":
            return {"query": task, "document": context.get("document", "")}
        if name == "ticket_lookup":
            return {
                "ticket_id": context.get("ticket_id", "TKT-DEMO"),
                "customer_tier": context.get("customer_tier", "growth"),
                "status": context.get("status", "open"),
            }
        if name == "knowledge_base_search":
            return {"issue": context.get("issue", task)}
        if name == "policy_check":
            return {"risk": context.get("risk", "low"), "amount": context.get("amount", 0)}
        if name == "extract_fields":
            return {"text": context.get("text") or context.get("document") or task}
        if name == "validate_schema":
            extracted = next(
                (item["result"] for item in observations if item["tool"] == "extract_fields"), {}
            )
            return {"record": extracted, "required": context.get("required", [])}
        return context

    async def run(self, request: RunCreate, db: Session) -> AgentRun:
        profile = AGENT_PROFILES[request.agent_type]
        model = request.model or provider_registry.default_model(request.provider)
        scenario: GoldenScenario | None = (
            SCENARIO_INDEX.get(request.scenario_id) if request.scenario_id else None
        )
        run = AgentRun(
            task=request.task,
            agent_type=request.agent_type,
            provider=request.provider,
            model=model,
            scenario_id=request.scenario_id,
        )
        db.add(run)
        db.flush()
        run_started = time.perf_counter()

        root = TraceSpan(
            run_id=run.id,
            kind="agent",
            name=profile.name,
            input={"task": request.task, "context_keys": sorted(request.context)},
            attributes={"provider": request.provider, "model": model},
        )
        db.add(root)
        db.flush()

        actual_tools: list[str] = []
        observations: list[dict[str, Any]] = []
        execution_error: str | None = None

        try:
            planner_started = time.perf_counter()
            planned_tools = choose_tools(request.agent_type, request.task)
            run.planner_output = {
                "strategy": "plan → observe → synthesize → evaluate",
                "steps": [
                    {"index": index + 1, "tool": name, "reason": self.tools.get(name).description}
                    for index, name in enumerate(planned_tools)
                ],
            }
            planner_ended = _utcnow()
            db.add(
                TraceSpan(
                    run_id=run.id,
                    parent_id=root.id,
                    kind="planner",
                    name="deterministic_planner",
                    started_at=root.started_at,
                    ended_at=planner_ended,
                    duration_ms=max(1, int((time.perf_counter() - planner_started) * 1000)),
                    input={"task": request.task, "available_tools": list(profile.tools)},
                    output=run.planner_output,
                    attributes={"step_count": len(planned_tools)},
                )
            )

            for tool_name in planned_tools:
                tool_started_at = _utcnow()
                tool_started = time.perf_counter()
                payload = self._payload_for_tool(
                    tool_name, request.task, request.context, observations
                )
                result = self.tools.execute(tool_name, payload)
                duration = max(1, int((time.perf_counter() - tool_started) * 1000))
                observations.append({"tool": tool_name, "result": result})
                actual_tools.append(tool_name)
                db.add(
                    TraceSpan(
                        run_id=run.id,
                        parent_id=root.id,
                        kind="tool",
                        name=tool_name,
                        started_at=tool_started_at,
                        ended_at=_utcnow(),
                        duration_ms=duration,
                        input=payload,
                        output=result,
                        attributes={"attempt": 1},
                    )
                )

            prompt_packet: dict[str, Any] = {
                "task": request.task,
                "observations": observations,
                "output_contract": (
                    "Return a concise, evidence-grounded answer. Prefer JSON for structured tasks."
                ),
            }
            if request.provider == "mock" and scenario:
                prompt_packet["expected"] = scenario.expected
            model_started_at = _utcnow()
            model_started = time.perf_counter()
            response = await provider_registry.get(request.provider).generate(
                system=profile.instructions,
                prompt=json.dumps(prompt_packet, sort_keys=True),
                model=model,
            )
            model_duration = max(1, int((time.perf_counter() - model_started) * 1000))
            run.final_output = response.text
            run.input_tokens = response.input_tokens
            run.output_tokens = response.output_tokens
            run.cost_usd = provider_registry.cost(
                request.provider, response.input_tokens, response.output_tokens
            )
            db.add(
                TraceSpan(
                    run_id=run.id,
                    parent_id=root.id,
                    kind="model",
                    name=f"{request.provider}:{model}",
                    started_at=model_started_at,
                    ended_at=_utcnow(),
                    duration_ms=model_duration,
                    input={"prompt_chars": len(json.dumps(prompt_packet))},
                    output={"text": response.text, "finish_reason": response.finish_reason},
                    attributes={
                        "input_tokens": response.input_tokens,
                        "output_tokens": response.output_tokens,
                        "cost_usd": run.cost_usd,
                    },
                )
            )
            run.status = "completed"
        except Exception as exc:  # A failed run is still valuable evaluation evidence.
            execution_error = str(exc)
            run.status = "failed"
            run.error = execution_error
            run.final_output = ""

        eval_started_at = _utcnow()
        eval_started = time.perf_counter()
        result = evaluate_run(
            output=run.final_output,
            actual_tools=actual_tools,
            scenario=scenario,
            execution_error=execution_error,
        )
        evaluation = Evaluation(
            run_id=run.id,
            evaluator_type=result.evaluator_type,
            score=result.score,
            passed=result.passed,
            task_completion=result.task_completion,
            answer_correctness=result.answer_correctness,
            tool_selection_accuracy=result.tool_selection_accuracy,
            hallucination_rate=result.hallucination_rate,
            failure_classification=result.failure_classification,
            rubric=result.rubric,
            rationale=result.rationale,
        )
        db.add(evaluation)
        db.add(
            TraceSpan(
                run_id=run.id,
                parent_id=root.id,
                kind="evaluator",
                name=result.evaluator_type,
                started_at=eval_started_at,
                ended_at=_utcnow(),
                duration_ms=max(1, int((time.perf_counter() - eval_started) * 1000)),
                input={"scenario_id": request.scenario_id, "actual_tools": actual_tools},
                output={
                    "score": result.score,
                    "passed": result.passed,
                    "failure_classification": result.failure_classification,
                },
                attributes={"rubric": result.rubric},
            )
        )
        run.latency_ms = max(1, int((time.perf_counter() - run_started) * 1000))
        run.completed_at = _utcnow()
        run.escalated = any(
            observation["result"].get("requires_approval", False)
            for observation in observations
            if isinstance(observation.get("result"), dict)
        )
        root.ended_at = run.completed_at
        root.duration_ms = run.latency_ms
        root.status = "ok" if run.status == "completed" else "error"
        root.output = {"status": run.status, "score": result.score}
        db.commit()
        db.refresh(run)
        return run
