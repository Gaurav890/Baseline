from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.agents.profiles import AGENT_PROFILES, choose_tools
from app.config import settings
from app.evals.catalog import SCENARIO_INDEX, GoldenScenario
from app.evals.evaluators import evaluate_run
from app.evals.workflows import SHOWCASE_SCENARIO_INDEX
from app.models import AgentRun, Evaluation, TraceSpan
from app.providers.registry import provider_registry
from app.schemas import RunCreate
from app.services.safety import (
    SafetyPolicyViolation,
    enforce_plan,
    enforce_run_start,
    enforce_runtime,
    enforce_tool,
    record_approval_signal,
    record_safety_event,
    safety_policy_for,
)
from app.tools.registry import ToolRegistry, default_tools


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AgentRunner:
    def __init__(self, tools: ToolRegistry = default_tools) -> None:
        self.tools = tools

    @staticmethod
    def _output_schema(scenario: GoldenScenario | None) -> dict[str, str] | None:
        if scenario is None:
            return None
        return {
            key: (
                "null"
                if value is None
                else "number"
                if isinstance(value, (int, float)) and not isinstance(value, bool)
                else "boolean"
                if isinstance(value, bool)
                else "array"
                if isinstance(value, list)
                else "object"
                if isinstance(value, dict)
                else "string"
            )
            for key, value in scenario.expected.items()
        }

    @staticmethod
    def _payload_for_tool(
        name: str, task: str, context: dict[str, Any], observations: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if name == "web_search":
            payload = {"query": task, "sources": context.get("sources", [])}
            for key in (
                "connector",
                "cik",
                "operation",
                "owner",
                "repo",
                "repository",
                "issue_number",
                "state",
                "max_results",
            ):
                if key in context:
                    payload[key] = context[key]
            return payload
        if name == "document_search":
            return {
                "query": task,
                "document": context.get("document", ""),
                **({"url": context["url"]} if context.get("url") else {}),
            }
        if name == "ticket_lookup":
            return {
                "ticket_id": context.get("ticket_id", "TKT-DEMO"),
                "customer_tier": context.get("customer_tier", "growth"),
                "status": context.get("status", "open"),
                **(
                    {"snapshot": context["ticket_snapshot"]}
                    if context.get("ticket_snapshot")
                    else {}
                ),
            }
        if name == "knowledge_base_search":
            return {
                "issue": context.get("issue", task),
                **(
                    {"snapshot": context["knowledge_snapshot"]}
                    if context.get("knowledge_snapshot")
                    else {}
                ),
            }
        if name == "policy_check":
            assessment = next(
                (
                    item["result"]
                    for item in reversed(observations)
                    if item["tool"] == "risk_assessment"
                ),
                {},
            )
            return {
                "risk": assessment.get("risk_level", context.get("risk", "low")),
                "amount": context.get("amount", 0),
            }
        if name == "extract_fields":
            return {"text": context.get("text") or context.get("document") or task}
        if name == "validate_schema":
            extracted = next(
                (item["result"] for item in observations if item["tool"] == "extract_fields"), {}
            )
            return {"record": extracted, "required": context.get("required", [])}
        if name == "sec_company_lookup":
            return {
                "query": task,
                "cik": context.get("cik", ""),
                "operation": context.get("sec_operation", "submissions"),
                **({"snapshot": context["sec_snapshot"]} if context.get("sec_snapshot") else {}),
            }
        if name == "github_issue_lookup":
            return {
                "query": task,
                "repository": context.get("repository", ""),
                "operation": context.get("github_operation", "issue"),
                "issue_number": context.get("issue_number", 1),
                "max_results": context.get("max_results", 10),
                "state": context.get("state", "open"),
                **(
                    {"snapshot": context["github_snapshot"]}
                    if context.get("github_snapshot")
                    else {}
                ),
            }
        if name == "web_intelligence":
            return {
                "query": context.get("web_query", task),
                "max_results": context.get("max_results", 5),
                **({"sources": context["web_sources"]} if context.get("web_sources") else {}),
            }
        if name == "incident_context":
            return {
                "snapshot": context.get("incident", {}),
                "provenance": context.get("incident_provenance", "SNAPSHOT"),
            }
        if name == "risk_assessment":
            return {
                "risk_factors": context.get("risk_factors", []),
                "observations": [dict(observation) for observation in observations],
            }
        return context

    async def run(
        self,
        request: RunCreate,
        db: Session,
        *,
        experiment_candidate_id: str | None = None,
        experiment_case_key: str | None = None,
    ) -> AgentRun:
        profile = AGENT_PROFILES[request.agent_type]
        model = request.model or provider_registry.default_model(request.provider)
        scenario: GoldenScenario | None = (
            (SCENARIO_INDEX | SHOWCASE_SCENARIO_INDEX).get(request.scenario_id)
            if request.scenario_id
            else None
        )
        task = scenario.task if scenario else request.task
        context = scenario.context if scenario else request.context
        policy = safety_policy_for(db, request.agent_type)
        run = AgentRun(
            task=task,
            agent_type=request.agent_type,
            provider=request.provider,
            model=model,
            scenario_id=request.scenario_id,
            experiment_candidate_id=experiment_candidate_id,
            experiment_case_key=experiment_case_key,
        )
        db.add(run)
        db.flush()
        run_started = time.perf_counter()

        root = TraceSpan(
            run_id=run.id,
            kind="agent",
            name=profile.name,
            input={
                "task": task,
                "context_keys": sorted(context),
                "catalog_context": scenario is not None,
            },
            attributes={
                "provider": request.provider,
                "model": model,
                "safety_policy_id": policy.id,
                "safety_policy_version": policy.version,
            },
        )
        db.add(root)
        db.flush()

        actual_tools: list[str] = []
        observations: list[dict[str, Any]] = []
        execution_error: str | None = None

        try:
            enforce_run_start(
                db,
                run_id=run.id,
                policy=policy,
                provider=request.provider,
            )
            planner_started = time.perf_counter()
            planned_tools = choose_tools(request.agent_type, task)
            enforce_plan(
                db,
                run_id=run.id,
                policy=policy,
                planned_tools=planned_tools,
            )
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
                    input={"task": task, "available_tools": list(profile.tools)},
                    output=run.planner_output,
                    attributes={"step_count": len(planned_tools)},
                )
            )

            for tool_name in planned_tools:
                enforce_runtime(
                    db,
                    run_id=run.id,
                    policy=policy,
                    run_started=run_started,
                    action=tool_name,
                )
                tool_started_at = _utcnow()
                tool_started = time.perf_counter()
                payload = self._payload_for_tool(
                    tool_name, task, context, observations
                )
                if tool_name == "policy_check":
                    payload["approval_threshold_usd"] = policy.approval_threshold_usd
                tool = self.tools.get(tool_name)
                enforce_tool(
                    db,
                    run_id=run.id,
                    policy=policy,
                    tool=tool,
                    payload=payload,
                    call_number=len(actual_tools) + 1,
                )
                try:
                    execution = self.tools.execute_with_metadata(tool_name, payload)
                except Exception as exc:
                    duration = max(1, int((time.perf_counter() - tool_started) * 1000))
                    db.add(
                        TraceSpan(
                            run_id=run.id,
                            parent_id=root.id,
                            kind="tool",
                            name=tool_name,
                            status="error",
                            started_at=tool_started_at,
                            ended_at=_utcnow(),
                            duration_ms=duration,
                            input=payload,
                            output={"error": str(exc), "error_type": type(exc).__name__},
                            attributes={"attempt": 1},
                        )
                    )
                    record_safety_event(
                        db,
                        run_id=run.id,
                        policy=policy,
                        category="containment",
                        decision="tool_error",
                        action=tool_name,
                        reason=f"{type(exc).__name__}: {exc}"[:2000],
                        payload=payload,
                    )
                    raise
                result = execution.output
                duration = max(1, int((time.perf_counter() - tool_started) * 1000))
                observations.append({"tool": tool_name, "result": result})
                actual_tools.append(tool_name)
                record_approval_signal(
                    db,
                    run_id=run.id,
                    policy=policy,
                    action=tool_name,
                    output=result,
                )
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
                        attributes={
                            "attempt": 1,
                            **execution.metadata.as_trace_attributes(),
                        },
                    )
                )

            prompt_packet: dict[str, Any] = {
                "task": task,
                "observations": observations,
                "output_contract": {
                    "format": "json",
                    "schema": self._output_schema(scenario),
                    "instructions": (
                        "Return one valid JSON object only. When a schema is supplied, use those "
                        "exact top-level keys and preserve the requested value types. Base every "
                        "claim on the observations; do not invent URLs or facts."
                    ),
                },
            }
            if request.provider == "mock" and scenario:
                prompt_packet["expected"] = scenario.expected
            model_started_at = _utcnow()
            model_started = time.perf_counter()
            enforce_runtime(
                db,
                run_id=run.id,
                policy=policy,
                run_started=run_started,
                action="model_generation",
            )
            remaining_runtime = max(
                0.001, policy.max_runtime_seconds - (time.perf_counter() - run_started)
            )
            model_timeout = min(settings.provider_timeout_seconds, remaining_runtime)
            try:
                response = await asyncio.wait_for(
                    provider_registry.get(request.provider).generate(
                        system=profile.instructions,
                        prompt=json.dumps(prompt_packet, sort_keys=True),
                        model=model,
                    ),
                    timeout=model_timeout,
                )
            except TimeoutError as exc:
                reason = f"Model generation exceeded its {model_timeout:.3f}s deadline"
                record_safety_event(
                    db,
                    run_id=run.id,
                    policy=policy,
                    category="budget",
                    decision="timed_out",
                    action="model_generation",
                    reason=reason,
                )
                raise SafetyPolicyViolation("budget", reason, decision="timed_out") from exc
            model_duration = max(1, int((time.perf_counter() - model_started) * 1000))
            run.final_output = response.text
            run.input_tokens = response.input_tokens
            run.output_tokens = response.output_tokens
            run.cost_usd = provider_registry.cost(
                request.provider, response.input_tokens, response.output_tokens
            )
            if run.cost_usd > policy.max_estimated_cost_usd:
                reason = (
                    f"Actual provider cost {run.cost_usd:.6f} exceeded the "
                    f"{policy.max_estimated_cost_usd:.6f} run ceiling"
                )
                record_safety_event(
                    db,
                    run_id=run.id,
                    policy=policy,
                    category="budget",
                    decision="budget_exceeded",
                    action="model_generation",
                    reason=reason,
                    metadata={"actual_cost_usd": run.cost_usd},
                )
                raise SafetyPolicyViolation("budget", reason)
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
            execution_error = (
                f"safety_policy:{exc.category}: {exc}"
                if isinstance(exc, SafetyPolicyViolation)
                else str(exc)
            )
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
        ) or bool(execution_error and execution_error.startswith("safety_policy:authority"))
        root.ended_at = run.completed_at
        root.duration_ms = run.latency_ms
        root.status = "ok" if run.status == "completed" else "error"
        root.output = {"status": run.status, "score": result.score}
        db.commit()
        db.refresh(run)
        return run
