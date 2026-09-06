from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GoldenScenario:
    id: str
    category: str
    task: str
    context: dict[str, Any]
    expected: dict[str, Any]
    expected_tools: tuple[str, ...]
    tags: tuple[str, ...]


def _research_scenarios() -> list[GoldenScenario]:
    topics = [
        "vendor concentration risk",
        "incident response readiness",
        "enterprise AI adoption",
        "cloud cost controls",
        "software supply-chain risk",
    ]
    scenarios: list[GoldenScenario] = []
    for index in range(55):
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
    _research_scenarios()
    + _document_scenarios()
    + _support_scenarios()
    + _extraction_scenarios()
)
SCENARIO_INDEX = {scenario.id: scenario for scenario in GOLDEN_SCENARIOS}

assert len(GOLDEN_SCENARIOS) == 218
