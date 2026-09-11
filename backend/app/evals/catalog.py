from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GOLDEN_DATASET_VERSION = "golden-v2"


@dataclass(frozen=True)
class GoldenScenario:
    id: str
    category: str
    task: str
    context: dict[str, Any]
    expected: dict[str, Any]
    expected_tools: tuple[str, ...]
    tags: tuple[str, ...]
    fixture_kind: str = "synthetic"
    blocking: bool = True
    oracle_type: str = "deterministic"
    success_criteria: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()


def _public_snapshot_scenarios() -> list[GoldenScenario]:
    path = Path(__file__).with_name("snapshots") / "public_sources.v1.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    scenarios: list[GoldenScenario] = []
    scenario_index = 1
    for source in snapshot["sources"]:
        fields = source["selected_fields"]
        source_context = {
            "title": source["key"],
            "url": source["url"],
            "snippet": json.dumps(fields, sort_keys=True),
            "connector": source["connector"],
            "retrieved_at": snapshot["retrieved_at"],
            "content_hash": source["content_sha256"],
            "provenance": snapshot["provenance"],
            "snapshot_version": snapshot["snapshot_version"],
        }
        sources = [source_context]
        if fields.get("html_url"):
            sources.append(
                {
                    "title": f"{source['key']}-web",
                    "url": fields["html_url"],
                    "snippet": fields["title"],
                    "connector": source["connector"],
                    "retrieved_at": snapshot["retrieved_at"],
                    "content_hash": source["content_sha256"],
                    "provenance": snapshot["provenance"],
                    "snapshot_version": snapshot["snapshot_version"],
                }
            )

        if source["connector"] == "sec_edgar":
            variants = [
                (
                    (
                        "Identify the registrant, CIK, and ticker from this frozen SEC filing "
                        "snapshot."
                    ),
                    {key: fields[key] for key in ("name", "cik", "ticker")},
                ),
                (
                    "Create a sourced report of the SEC industry classification in this snapshot.",
                    {key: fields[key] for key in ("sic", "sic_description", "filer_category")},
                ),
                (
                    "Summarize the registrant market identifiers from this SEC filing evidence.",
                    {key: fields[key] for key in ("ticker", "exchange", "entity_type")},
                ),
                (
                    (
                        "Report the latest filing form, date, and accession in this frozen SEC "
                        "snapshot."
                    ),
                    {
                        "form": fields["latest_form"],
                        "filing_date": fields["latest_filing_date"],
                        "accession": fields["latest_accession"],
                    },
                ),
            ]
            expected_tools = ("web_search", "document_search")
        else:
            variants = [
                (
                    (
                        "Identify the repository, issue number, and title from this frozen GitHub "
                        "evidence."
                    ),
                    {key: fields[key] for key in ("repository", "number", "title")},
                ),
                (
                    "State the issue status and author using only this frozen GitHub evidence.",
                    {key: fields[key] for key in ("state", "author")},
                ),
                (
                    "Give the creation and closure timestamps from this frozen GitHub evidence.",
                    {key: fields[key] for key in ("created_at", "closed_at")},
                ),
                (
                    "Return the public issue URL from this frozen GitHub evidence.",
                    {"html_url": fields["html_url"]},
                ),
            ]
            expected_tools = ("web_search",)

        for task, expected in variants:
            scenarios.append(
                GoldenScenario(
                    id=f"research-{scenario_index:03d}",
                    category="research",
                    task=task,
                    context={"sources": sources, "document": json.dumps(fields, sort_keys=True)},
                    expected=expected,
                    expected_tools=expected_tools,
                    tags=(
                        "grounding",
                        "public-source",
                        "frozen-regression",
                        "snapshot",
                        source["connector"],
                    ),
                    fixture_kind="frozen_snapshot",
                    blocking=True,
                )
            )
            scenario_index += 1
    return scenarios


def _research_scenarios() -> list[GoldenScenario]:
    topics = [
        "vendor concentration risk",
        "incident response readiness",
        "enterprise AI adoption",
        "cloud cost controls",
        "software supply-chain risk",
    ]
    scenarios: list[GoldenScenario] = _public_snapshot_scenarios()
    # Twelve generic synthetic research cases were replaced by public GitHub
    # pull-request scenarios in golden-v2. The release gate remains exactly 218.
    for index in range(len(scenarios), 43):
        topic = topics[index % len(topics)]
        use_document = index % 4 == 0
        noun = "report" if use_document else "brief"
        tools = ("web_search", "document_search") if use_document else ("web_search",)
        scenarios.append(
            GoldenScenario(
                id=f"research-{index + 1:03d}",
                category="research",
                task=f"Create an evidence-backed {noun} on {topic}; separate facts from inference.",
                context={
                    "sources": [
                        {
                            "title": f"Primary evidence {index + 1}",
                            "url": f"https://evidence.example/research/{index + 1}",
                            "snippet": f"Verified finding about {topic}.",
                            "provenance": "SYNTHETIC",
                        }
                    ],
                    "document": (
                        f"The source report identifies {topic} as a monitored control area."
                    ),
                },
                expected={
                    "answer": f"The evidence identifies {topic} as a monitored control area.",
                    "confidence": "high",
                    "sources": [f"https://evidence.example/research/{index + 1}"],
                },
                expected_tools=tools,
                tags=("grounding", "citations", "research"),
            )
        )
    return scenarios


def _github_pull_request_scenarios() -> list[GoldenScenario]:
    path = Path(__file__).with_name("snapshots") / "github_pull_requests.v1.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    scenarios: list[GoldenScenario] = []
    index = 1
    policies = {
        "github-flask-pr-5917": {
            "risk": "medium",
            "decision": "review",
            "approval_required": False,
            "risk_factors": [
                {"name": "one captured check did not pass", "severity": "medium"},
                {"name": "no review evidence was captured", "severity": "medium"},
            ],
            "blocking_reasons": [
                "Development Versions check failed",
                "No review evidence captured",
            ],
        },
        "github-httpx-pr-2185": {
            "risk": "high",
            "decision": "hold",
            "approval_required": True,
            "risk_factors": [
                {"name": "no check-run evidence was captured", "severity": "high"},
                {"name": "URL-handling code changed", "severity": "medium"},
            ],
            "blocking_reasons": ["No check-run evidence captured"],
        },
        "github-fastapi-pr-11560": {
            "risk": "high",
            "decision": "hold",
            "approval_required": True,
            "risk_factors": [
                {"name": "GitHub reported an unstable merge state", "severity": "high"},
                {"name": "authentication code changed", "severity": "high"},
            ],
            "blocking_reasons": [
                "Merge state was unstable",
                "Authentication-sensitive code changed",
            ],
        },
    }
    for source in snapshot["sources"]:
        fields = source["selected_fields"]
        policy = policies[source["key"]]
        source_records = [
            {
                "title": f"{source['key']}:{position}",
                "url": url,
                "snippet": json.dumps(fields, sort_keys=True),
                "connector": "github",
                "retrieved_at": snapshot["retrieved_at"],
                "content_hash": source["content_sha256"],
                "provenance": "SNAPSHOT",
                "snapshot_version": snapshot["snapshot_version"],
            }
            for position, url in enumerate(source["source_urls"], start=1)
        ]
        context = {
            "repository": fields["repository"],
            "pull_number": fields["number"],
            "github_operation": "pull_request",
            "github_snapshot": {
                "title": source["key"],
                "source_url": fields["html_url"],
                "source_urls": source["source_urls"],
                "content_hash": source["content_sha256"],
                "snapshot_version": snapshot["snapshot_version"],
                "data": fields,
            },
            "sources": source_records,
            "risk": policy["risk"],
            "risk_factors": policy["risk_factors"],
        }
        variants = (
            (
                (
                    "Make a release decision from this frozen GitHub pull-request evidence. "
                    "Treat missing or conflicting evidence as risk and do not change repository "
                    "state."
                ),
                {
                    "repository": fields["repository"],
                    "pull_number": fields["number"],
                    "decision": policy["decision"],
                    "risk_level": policy["risk"],
                    "blocking_reasons": policy["blocking_reasons"],
                    "approval_required": policy["approval_required"],
                },
                (
                    "Uses frozen PR, review, changed-file, and check evidence",
                    "Applies the published release-risk policy",
                    "Does not claim that a repository action was executed",
                ),
            ),
            (
                "Report the change surface and identify security-sensitive files in this PR.",
                {
                    "repository": fields["repository"],
                    "pull_number": fields["number"],
                    "changed_files": fields["changed_files"],
                    "test_files_changed": fields["test_files_changed"],
                    "security_sensitive_files": fields["security_sensitive_files"],
                },
                (
                    "Reports the exact changed-file count",
                    "Identifies sensitive paths from the frozen evidence",
                ),
            ),
            (
                "Assess whether the captured review evidence is sufficient for release.",
                {
                    "repository": fields["repository"],
                    "pull_number": fields["number"],
                    "review_count": fields["review_count"],
                    "approvals": fields["approvals"],
                    "changes_requested": fields["changes_requested"],
                    "decision": policy["decision"],
                },
                (
                    "Distinguishes approvals from comments",
                    "Does not equate a merged historical state with complete evidence",
                ),
            ),
            (
                "Assess the captured CI checks and return the evidence-based release decision.",
                {
                    "repository": fields["repository"],
                    "pull_number": fields["number"],
                    "check_count": fields["check_count"],
                    "successful_checks": fields["successful_checks"],
                    "failed_checks": fields["failed_checks"],
                    "pending_checks": fields["pending_checks"],
                    "decision": policy["decision"],
                },
                (
                    "Reports success, failure, and pending check evidence separately",
                    "Treats absent CI evidence as unknown rather than passing",
                ),
            ),
        )
        for task, expected, criteria in variants:
            scenarios.append(
                GoldenScenario(
                    id=f"release-risk-{index:03d}",
                    category="release_risk",
                    task=task,
                    context=context,
                    expected=expected,
                    expected_tools=(
                        "github_issue_lookup",
                        "risk_assessment",
                        "policy_check",
                    ),
                    tags=(
                        "github",
                        "pull-request",
                        "public-source",
                        "frozen-regression",
                        "release-decision",
                    ),
                    fixture_kind="frozen_snapshot",
                    blocking=True,
                    oracle_type="public-evidence+policy-invariants",
                    success_criteria=criteria,
                    forbidden_tools=("github_write", "merge_pull_request"),
                )
            )
            index += 1
    return scenarios


def _document_scenarios() -> list[GoldenScenario]:
    clauses = ["renewal", "termination", "data retention", "service level", "indemnity"]
    scenarios: list[GoldenScenario] = []
    for index in range(55):
        clause = clauses[index % len(clauses)]
        extraction = index % 3 == 0
        verb = "Extract the date and summarize" if extraction else "Summarize"
        tools = ("document_search", "extract_fields") if extraction else ("document_search",)
        scenarios.append(
            GoldenScenario(
                id=f"document-{index + 1:03d}",
                category="document",
                task=f"{verb} the {clause} clause in this document.",
                context={
                    "document": (
                        f"Agreement effective 2026-01-15. The {clause} clause requires 30 days' "
                        "written notice. No other obligation is stated."
                    )
                },
                expected={
                    "clause": clause,
                    "summary": "Requires 30 days' written notice.",
                    "effective_date": "2026-01-15" if extraction else None,
                },
                expected_tools=tools,
                tags=("document", "grounding", "precision"),
            )
        )
    return scenarios


def _support_scenarios() -> list[GoldenScenario]:
    issues = ["login failure", "sync delay", "billing mismatch", "API timeout", "data export"]
    scenarios: list[GoldenScenario] = []
    for index in range(54):
        issue = issues[index % len(issues)]
        gated = index % 4 == 0
        action = "credit review" if gated else "resolution"
        task = f"Resolve ticket TKT-{1000 + index} for a {issue} and provide a safe {action}."
        if gated:
            task += " The customer requests a refund after an outage."
        tools = (
            ("ticket_lookup", "knowledge_base_search", "policy_check")
            if gated
            else ("ticket_lookup", "knowledge_base_search")
        )
        scenarios.append(
            GoldenScenario(
                id=f"support-{index + 1:03d}",
                category="support",
                task=task,
                context={
                    "ticket_id": f"TKT-{1000 + index}",
                    "status": "open",
                    "customer_tier": "enterprise" if gated else "growth",
                    "issue": issue,
                    "amount": 750 if gated else 0,
                    "risk": "high" if gated else "low",
                    "ticket_snapshot": {
                        "ticket_id": f"TKT-{1000 + index}",
                        "customer_tier": "enterprise" if gated else "growth",
                        "status": "open",
                        "prior_contacts": 1,
                        "entitlements": ["email_support", "service_credit_review"],
                    },
                    "knowledge_snapshot": {
                        "issue": issue,
                        "articles": [
                            {
                                "id": "KB-104",
                                "title": "Connection recovery checklist",
                                "confidence": 0.94,
                            },
                            {
                                "id": "KB-219",
                                "title": "Escalation and service-credit policy",
                                "confidence": 0.86,
                            },
                        ],
                    },
                },
                expected={
                    "ticket_id": f"TKT-{1000 + index}",
                    "resolution": "Follow KB-104 and confirm recovery.",
                    "escalate": gated,
                },
                expected_tools=tools,
                tags=("support", "policy", "multi-tool"),
            )
        )
    return scenarios


def _extraction_scenarios() -> list[GoldenScenario]:
    scenarios: list[GoldenScenario] = []
    for index in range(54):
        amount = 1000 + index * 17
        date = f"2026-{(index % 9) + 1:02d}-{(index % 27) + 1:02d}"
        email = f"owner{index + 1}@example.com"
        text = f"Record owner {email}; renewal date {date}; contract value ${amount}.00."
        scenarios.append(
            GoldenScenario(
                id=f"extraction-{index + 1:03d}",
                category="extraction",
                task="Extract the email, date, and amount fields, then validate the record schema.",
                context={"text": text, "required": ["emails", "dates", "amounts"]},
                expected={"emails": [email], "dates": [date], "amounts": [float(amount)]},
                expected_tools=("extract_fields", "validate_schema"),
                tags=("extraction", "schema", "structured-output"),
            )
        )
    return scenarios


GOLDEN_SCENARIOS = tuple(
    _github_pull_request_scenarios()
    + _research_scenarios()
    + _document_scenarios()
    + _support_scenarios()
    + _extraction_scenarios()
)
SCENARIO_INDEX = {scenario.id: scenario for scenario in GOLDEN_SCENARIOS}

# Live canaries exercise the current external APIs separately from the deterministic PR gate.
# They are intentionally not members of GOLDEN_SCENARIOS and cannot block a merge.
LIVE_CANARIES = (
    {
        "id": "canary-sec-apple-submissions",
        "connector": "sec_edgar",
        "source_url": "https://data.sec.gov/submissions/CIK0000320193.json",
        "blocking": False,
        "provenance": "LIVE",
    },
    {
        "id": "canary-github-flask-issue",
        "connector": "github",
        "source_url": "https://api.github.com/repos/pallets/flask/issues/1",
        "blocking": False,
        "provenance": "LIVE",
    },
)

assert len(GOLDEN_SCENARIOS) == 218
