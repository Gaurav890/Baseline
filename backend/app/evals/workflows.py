from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.evals.catalog import GoldenScenario

WORKFLOW_DATASET_VERSION = "workflow-showcase-v1"


def _public_sources() -> dict[str, dict[str, Any]]:
    path = Path(__file__).with_name("snapshots") / "public_sources.v1.json"
    packet = json.loads(path.read_text(encoding="utf-8"))
    return {str(source["key"]): source for source in packet["sources"]}


def _frozen_source(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": source["key"],
        "url": source["url"],
        "snippet": json.dumps(source["selected_fields"], sort_keys=True),
        "provenance": "SNAPSHOT",
        "snapshot_version": "public-sources-v1",
        "content_hash": source["content_sha256"],
    }


def _connector_snapshot(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": source["key"],
        "source_url": source["url"],
        "content_hash": source["content_sha256"],
        "snapshot_version": "public-sources-v1",
        "data": source["selected_fields"],
    }


def _synthetic_signal(key: str, snippet: str) -> dict[str, Any]:
    return {
        "title": key,
        "url": f"https://evidence.example/workflows/{key}",
        "snippet": snippet,
        "provenance": "SYNTHETIC",
        "snapshot_version": WORKFLOW_DATASET_VERSION,
    }


def _build_showcase() -> tuple[GoldenScenario, ...]:
    sources = _public_sources()
    apple = sources["sec-apple-submissions"]
    microsoft = sources["sec-microsoft-submissions"]
    flask = sources["github-flask-issue-1"]
    fastapi = sources["github-fastapi-issue-1"]

    cases: list[GoldenScenario] = []
    for slug, source, severity in (
        ("apple", apple, "medium"),
        ("microsoft", microsoft, "low"),
    ):
        fields = source["selected_fields"]
        signal = _synthetic_signal(
            f"{slug}-procurement-review",
            f"Training-only procurement signal for {fields['name']}; not production evidence.",
        )
        evidence = [_frozen_source(source), signal]
        cases.append(
            GoldenScenario(
                id=f"due-diligence-{slug}-001",
                category="due_diligence",
                task=(
                    f"Assess {fields['name']} for procurement. Verify the public company record, "
                    "reconcile the supplied screening signal, score risk, and decide whether a "
                    "person must approve."
                ),
                context={
                    "cik": fields["cik"],
                    "sec_snapshot": _connector_snapshot(source),
                    "web_sources": [signal],
                    "sources": evidence,
                    "risk_factors": [
                        {"name": "procurement screening signal", "severity": severity}
                    ],
                    "risk": severity,
                },
                expected={
                    "company": fields["name"],
                    "decision": "review" if severity == "medium" else "proceed",
                    "risk_level": severity,
                    "key_findings": [
                        f"SEC identity verified for CIK {fields['cik']}",
                        "Procurement signal is synthetic training evidence",
                    ],
                    "evidence": [item["url"] for item in evidence],
                    "approval_required": False,
                },
                expected_tools=(
                    "sec_company_lookup",
                    "web_intelligence",
                    "risk_assessment",
                    "policy_check",
                ),
                tags=("workflow", "due-diligence", "multi-source", "human-gate"),
                fixture_kind="mixed_snapshot",
                blocking=False,
            )
        )

    for slug, source, severity in (
        ("flask", flask, "low"),
        ("fastapi", fastapi, "high"),
    ):
        fields = source["selected_fields"]
        signal = _synthetic_signal(
            f"{slug}-release-signal",
            "Training-only release signal used to exercise the risk gate; not a live advisory.",
        )
        evidence = [_frozen_source(source), signal]
        cases.append(
            GoldenScenario(
                id=f"release-risk-{slug}-001",
                category="release_risk",
                task=(
                    f"Evaluate release readiness for {fields['repository']} using the frozen issue "
                    "and supplied external signal. Return a go, hold, or review decision."
                ),
                context={
                    "repository": fields["repository"],
                    "issue_number": fields["number"],
                    "github_snapshot": _connector_snapshot(source),
                    "web_sources": [signal],
                    "sources": evidence,
                    "risk_factors": [{"name": "release readiness signal", "severity": severity}],
                    "risk": severity,
                },
                expected={
                    "repository": fields["repository"],
                    "decision": "hold" if severity == "high" else "proceed",
                    "risk_level": severity,
                    "blocking_issues": [fields["title"]] if severity == "high" else [],
                    "evidence": [item["url"] for item in evidence],
                    "approval_required": severity == "high",
                },
                expected_tools=(
                    "github_issue_lookup",
                    "web_intelligence",
                    "risk_assessment",
                    "policy_check",
                ),
                tags=("workflow", "release-risk", "github", "human-gate"),
                fixture_kind="mixed_snapshot",
                blocking=False,
            )
        )

    for slug, source, severity in (
        ("api-degradation", flask, "high"),
        ("data-integrity", fastapi, "critical"),
    ):
        fields = source["selected_fields"]
        incident_id = f"INC-{slug.upper()}-001"
        incident = {
            "incident_id": incident_id,
            "service": "public-api",
            "symptom": slug.replace("-", " "),
            "customer_impact": "Elevated errors in the training scenario",
            "status": "investigating",
        }
        evidence = [_frozen_source(source)]
        cases.append(
            GoldenScenario(
                id=f"incident-response-{slug}-001",
                category="incident_response",
                task=(
                    f"Triage {incident_id}, correlate the supplied incident with repository "
                    "evidence, prioritize containment, and identify any approval boundary."
                ),
                context={
                    "incident": incident,
                    "incident_provenance": "SYNTHETIC",
                    "repository": fields["repository"],
                    "issue_number": fields["number"],
                    "github_snapshot": _connector_snapshot(source),
                    "sources": evidence,
                    "risk_factors": [{"name": slug.replace("-", " "), "severity": severity}],
                    "risk": severity,
                },
                expected={
                    "incident_id": incident_id,
                    "severity": severity,
                    "status": "human_review_required",
                    "containment_plan": [
                        "Limit blast radius",
                        "Validate recovery with observed telemetry",
                    ],
                    "evidence": [item["url"] for item in evidence],
                    "approval_required": True,
                },
                expected_tools=(
                    "incident_context",
                    "github_issue_lookup",
                    "risk_assessment",
                    "policy_check",
                ),
                tags=("workflow", "incident-response", "containment", "human-gate"),
                fixture_kind="mixed_snapshot",
                blocking=False,
            )
        )
    return tuple(cases)


SHOWCASE_SCENARIOS = _build_showcase()
SHOWCASE_SCENARIO_INDEX = {scenario.id: scenario for scenario in SHOWCASE_SCENARIOS}

assert len(SHOWCASE_SCENARIOS) == 6
assert all(not scenario.blocking for scenario in SHOWCASE_SCENARIOS)
