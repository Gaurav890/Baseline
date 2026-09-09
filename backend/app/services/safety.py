from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.agents.profiles import AGENT_PROFILES
from app.config import settings
from app.connectors.base import canonical_hash
from app.models import SafetyEvent, SafetyPolicy
from app.providers.registry import provider_registry
from app.schemas import SafetyPolicyUpdate
from app.tools.registry import Tool


class SafetyPolicyViolation(RuntimeError):
    def __init__(self, category: str, reason: str, *, decision: str = "blocked") -> None:
        super().__init__(reason)
        self.category = category
        self.decision = decision


DEFAULT_POLICY_LIMITS: dict[str, dict[str, int | float]] = {
    "research": {"max_tool_calls": 3, "max_runtime_seconds": 60, "max_cost": 2.0},
    "document": {"max_tool_calls": 3, "max_runtime_seconds": 45, "max_cost": 1.5},
    "support": {"max_tool_calls": 4, "max_runtime_seconds": 45, "max_cost": 1.5},
    "extraction": {"max_tool_calls": 3, "max_runtime_seconds": 30, "max_cost": 1.0},
}


SAFETY_CHECKS: tuple[dict[str, Any], ...] = (
    {
        "id": "unexpected-tool",
        "category": "scope",
        "label": "Unexpected tool",
        "status": "enforced",
    },
    {
        "id": "tool-call-budget",
        "category": "budget",
        "label": "Tool-call ceiling",
        "status": "enforced",
    },
    {
        "id": "estimated-spend",
        "category": "budget",
        "label": "Preflight spend ceiling",
        "status": "enforced",
    },
    {
        "id": "runtime-deadline",
        "category": "budget",
        "label": "Run and provider deadlines",
        "status": "enforced",
    },
    {
        "id": "private-network",
        "category": "containment",
        "label": "Private-network SSRF",
        "status": "enforced",
    },
    {
        "id": "off-domain-egress",
        "category": "containment",
        "label": "Off-domain URL retrieval",
        "status": "enforced",
    },
    {
        "id": "oversized-response",
        "category": "containment",
        "label": "Oversized connector response",
        "status": "enforced",
    },
    {
        "id": "high-risk-action",
        "category": "authority",
        "label": "Human approval threshold",
        "status": "monitored",
    },
    {
        "id": "emergency-disable",
        "category": "containment",
        "label": "Global execution kill switch",
        "status": "enforced",
    },
    {
        "id": "secret-redaction",
        "category": "auditability",
        "label": "Trace secret redaction",
        "status": "enforced",
    },
    {
        "id": "sandbox-escape",
        "category": "containment",
        "label": "Isolated execution sandbox",
        "status": "not_implemented",
    },
    {
        "id": "covert-channel",
        "category": "containment",
        "label": "Cross-run covert-channel detection",
        "status": "not_implemented",
    },
    {
        "id": "workspace-rbac",
        "category": "authority",
        "label": "Authenticated workspace roles",
        "status": "not_implemented",
    },
)


def ensure_default_safety_policies(db: Session) -> list[SafetyPolicy]:
    current = {policy.agent_type: policy for policy in db.scalars(select(SafetyPolicy)).all()}
    created = False
    for agent_type, profile in AGENT_PROFILES.items():
        if agent_type in current:
            continue
        limits = DEFAULT_POLICY_LIMITS[agent_type]
        policy = SafetyPolicy(
            agent_type=agent_type,
            allowed_tools=list(profile.tools),
            max_tool_calls=int(limits["max_tool_calls"]),
            max_runtime_seconds=int(limits["max_runtime_seconds"]),
            max_estimated_cost_usd=float(limits["max_cost"]),
        )
        db.add(policy)
        current[agent_type] = policy
        created = True
    if created:
        db.commit()
    return [current[agent_type] for agent_type in AGENT_PROFILES]


def safety_policy_for(db: Session, agent_type: str) -> SafetyPolicy:
    ensure_default_safety_policies(db)
    policy = db.scalar(select(SafetyPolicy).where(SafetyPolicy.agent_type == agent_type))
    if policy is None:
        raise SafetyPolicyViolation("scope", f"No safety policy exists for {agent_type}")
    return policy


def update_safety_policy(db: Session, agent_type: str, request: SafetyPolicyUpdate) -> SafetyPolicy:
    policy = safety_policy_for(db, agent_type)
    changes = request.model_dump(exclude_unset=True)
    if "allowed_tools" in changes:
        profile_tools = set(AGENT_PROFILES[agent_type].tools)
        unknown = set(changes["allowed_tools"]) - profile_tools
        if unknown:
            raise ValueError(
                "Policy tools must belong to the agent profile: " + ", ".join(sorted(unknown))
            )
    for name, value in changes.items():
        setattr(policy, name, value)
    policy.version += 1
    policy.updated_at = datetime.now(UTC)
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


def record_safety_event(
    db: Session,
    *,
    run_id: str | None,
    policy: SafetyPolicy | None,
    category: str,
    decision: str,
    action: str,
    reason: str,
    payload: Any | None = None,
    metadata: dict[str, Any] | None = None,
) -> SafetyEvent:
    event = SafetyEvent(
        run_id=run_id,
        policy_id=policy.id if policy else None,
        category=category,
        decision=decision,
        action=action,
        reason=reason,
        input_hash=canonical_hash(payload) if payload is not None else None,
        metadata_json=metadata or {},
    )
    db.add(event)
    return event


def enforce_run_start(
    db: Session,
    *,
    run_id: str,
    policy: SafetyPolicy,
    provider: str,
) -> None:
    if not settings.agent_execution_enabled:
        reason = "Agent execution is disabled by the environment kill switch"
        record_safety_event(
            db,
            run_id=run_id,
            policy=policy,
            category="containment",
            decision="blocked",
            action="start_run",
            reason=reason,
        )
        raise SafetyPolicyViolation("containment", reason)
    if not policy.enabled:
        reason = f"The {policy.agent_type} policy is disabled"
        record_safety_event(
            db,
            run_id=run_id,
            policy=policy,
            category="scope",
            decision="blocked",
            action="start_run",
            reason=reason,
        )
        raise SafetyPolicyViolation("scope", reason)
    estimated_cost = provider_registry.cost(
        provider,
        settings.experiment_estimated_input_tokens,
        settings.experiment_estimated_output_tokens,
    )
    if estimated_cost > policy.max_estimated_cost_usd:
        reason = (
            "Estimated $"
            + f"{estimated_cost:.4f} provider cost exceeds the $"
            + f"{policy.max_estimated_cost_usd:.4f} run ceiling"
        )
        record_safety_event(
            db,
            run_id=run_id,
            policy=policy,
            category="budget",
            decision="blocked",
            action="provider_preflight",
            reason=reason,
            metadata={"estimated_cost_usd": estimated_cost},
        )
        raise SafetyPolicyViolation("budget", reason)
    record_safety_event(
        db,
        run_id=run_id,
        policy=policy,
        category="budget",
        decision="allowed",
        action="provider_preflight",
        reason="Estimated provider cost is within the per-run ceiling",
        metadata={"estimated_cost_usd": estimated_cost},
    )


def enforce_plan(
    db: Session,
    *,
    run_id: str,
    policy: SafetyPolicy,
    planned_tools: list[str],
) -> None:
    disallowed = [tool for tool in planned_tools if tool not in policy.allowed_tools]
    if disallowed:
        reason = f"Plan requested tools outside policy: {', '.join(disallowed)}"
        record_safety_event(
            db,
            run_id=run_id,
            policy=policy,
            category="scope",
            decision="blocked",
            action="plan_tools",
            reason=reason,
            payload=planned_tools,
        )
        raise SafetyPolicyViolation("scope", reason)
    if len(planned_tools) > policy.max_tool_calls:
        reason = (
            f"Plan requested {len(planned_tools)} tool calls; policy allows {policy.max_tool_calls}"
        )
        record_safety_event(
            db,
            run_id=run_id,
            policy=policy,
            category="budget",
            decision="blocked",
            action="plan_tools",
            reason=reason,
            payload=planned_tools,
        )
        raise SafetyPolicyViolation("budget", reason)
    record_safety_event(
        db,
        run_id=run_id,
        policy=policy,
        category="scope",
        decision="allowed",
        action="plan_tools",
        reason="Every planned tool is inside the agent contract",
        payload=planned_tools,
        metadata={"tool_calls": len(planned_tools)},
    )


def enforce_tool(
    db: Session,
    *,
    run_id: str,
    policy: SafetyPolicy,
    tool: Tool,
    payload: dict[str, Any],
    call_number: int,
) -> None:
    if tool.name not in policy.allowed_tools:
        reason = f"Tool {tool.name} is outside the {policy.agent_type} contract"
        category = "scope"
    elif tool.mutating and not policy.allow_mutating_tools:
        reason = f"Mutating tool {tool.name} is not authorized"
        category = "authority"
    elif call_number > policy.max_tool_calls:
        reason = f"Tool call {call_number} exceeds the {policy.max_tool_calls}-call budget"
        category = "budget"
    else:
        record_safety_event(
            db,
            run_id=run_id,
            policy=policy,
            category="authority" if tool.mutating else "scope",
            decision="allowed",
            action=tool.name,
            reason="Tool call is allowed by the active policy",
            payload=payload,
            metadata={"call_number": call_number, "mutating": tool.mutating},
        )
        return
    record_safety_event(
        db,
        run_id=run_id,
        policy=policy,
        category=category,
        decision="blocked",
        action=tool.name,
        reason=reason,
        payload=payload,
        metadata={"call_number": call_number, "mutating": tool.mutating},
    )
    raise SafetyPolicyViolation(category, reason)


def enforce_runtime(
    db: Session,
    *,
    run_id: str,
    policy: SafetyPolicy,
    run_started: float,
    action: str,
) -> None:
    elapsed = time.perf_counter() - run_started
    if elapsed <= policy.max_runtime_seconds:
        return
    reason = f"Run exceeded its {policy.max_runtime_seconds}s wall-clock budget before {action}"
    record_safety_event(
        db,
        run_id=run_id,
        policy=policy,
        category="budget",
        decision="timed_out",
        action=action,
        reason=reason,
        metadata={"elapsed_seconds": round(elapsed, 3)},
    )
    raise SafetyPolicyViolation("budget", reason, decision="timed_out")


def record_approval_signal(
    db: Session,
    *,
    run_id: str,
    policy: SafetyPolicy,
    action: str,
    output: dict[str, Any],
) -> None:
    if not output.get("requires_approval"):
        return
    record_safety_event(
        db,
        run_id=run_id,
        policy=policy,
        category="authority",
        decision="approval_required",
        action=action,
        reason=str(output.get("reason") or "Human approval is required"),
        metadata={"external_mutation_executed": False},
    )


def safety_snapshot(db: Session, *, event_limit: int = 50) -> dict[str, Any]:
    policies = ensure_default_safety_policies(db)
    events = list(
        db.scalars(select(SafetyEvent).order_by(desc(SafetyEvent.created_at)).limit(event_limit))
    )
    total_events = int(db.scalar(select(func.count(SafetyEvent.id))) or 0)
    blocked_events = int(
        db.scalar(
            select(func.count(SafetyEvent.id)).where(
                SafetyEvent.decision.in_(["blocked", "timed_out"])
            )
        )
        or 0
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "execution_enabled": settings.agent_execution_enabled,
        "summary": {
            "policy_count": len(policies),
            "event_count": total_events,
            "blocked_count": blocked_events,
            "mutating_tools_enabled": sum(policy.allow_mutating_tools for policy in policies),
        },
        "controls": [
            {"name": "Scope", "status": "enforced", "detail": "Per-agent tool allowlists"},
            {
                "name": "Authority",
                "status": "partial",
                "detail": "Approval signals; no write tools registered",
            },
            {
                "name": "Budget",
                "status": "enforced",
                "detail": "Calls, runtime, estimated provider cost",
            },
            {
                "name": "Verification",
                "status": "active",
                "detail": "218 regression cases plus human review",
            },
            {
                "name": "Containment",
                "status": "partial",
                "detail": "Restricted egress; no execution sandbox",
            },
            {
                "name": "Auditability",
                "status": "active",
                "detail": "Runs, spans, policy decisions, provenance",
            },
        ],
        "containment": {
            "https_only": True,
            "private_network_blocked": True,
            "redirects_blocked": True,
            "allowed_domains": list(settings.connector_allowed_domains),
            "max_response_bytes": settings.connector_max_response_bytes,
            "connector_timeout_seconds": settings.connector_timeout_seconds,
            "provider_timeout_seconds": settings.provider_timeout_seconds,
            "isolated_execution_sandbox": False,
            "covert_channel_detection": False,
        },
        "policies": policies,
        "events": events,
        "checks": list(SAFETY_CHECKS),
    }
