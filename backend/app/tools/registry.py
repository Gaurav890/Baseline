from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def describe(self, names: list[str]) -> list[dict[str, str]]:
        return [
            {"name": self.get(name).name, "description": self.get(name).description}
            for name in names
        ]

    def execute(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.get(name).handler(payload)


def _web_search(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query", ""))
    supplied = payload.get("sources", [])
    sources = supplied or [
        {
            "title": "Verified source packet",
            "url": "https://example.com/source",
            "snippet": f"Evidence relevant to: {query}",
        }
    ]
    return {"query": query, "results": sources[:5], "source_count": len(sources[:5])}


def _document_search(payload: dict[str, Any]) -> dict[str, Any]:
    document = str(payload.get("document", ""))
    query = str(payload.get("query", ""))
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", document) if part.strip()]
    terms = {word.lower() for word in re.findall(r"[A-Za-z]{4,}", query)}
    ranked = sorted(
        sentences,
        key=lambda sentence: sum(term in sentence.lower() for term in terms),
        reverse=True,
    )
    return {"query": query, "chunks": ranked[:3], "matches": min(3, len(ranked))}


def _ticket_lookup(payload: dict[str, Any]) -> dict[str, Any]:
    ticket_id = str(payload.get("ticket_id", "TKT-DEMO"))
    return {
        "ticket_id": ticket_id,
        "customer_tier": payload.get("customer_tier", "growth"),
        "status": payload.get("status", "open"),
        "prior_contacts": payload.get("prior_contacts", 1),
        "entitlements": payload.get("entitlements", ["email_support", "service_credit_review"]),
    }


def _knowledge_base_search(payload: dict[str, Any]) -> dict[str, Any]:
    issue = str(payload.get("issue", ""))
    return {
        "issue": issue,
        "articles": [
            {"id": "KB-104", "title": "Connection recovery checklist", "confidence": 0.94},
            {"id": "KB-219", "title": "Escalation and service-credit policy", "confidence": 0.86},
        ],
    }


def _extract_fields(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("text", ""))
    emails = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    money = re.findall(r"(?:\$|USD\s?)([\d,]+(?:\.\d{2})?)", text, re.IGNORECASE)
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
    return {
        "emails": emails,
        "amounts": [float(value.replace(",", "")) for value in money],
        "dates": dates,
        "record_count": max(len(emails), len(money), len(dates), 1 if text else 0),
    }


def _validate_schema(payload: dict[str, Any]) -> dict[str, Any]:
    record = payload.get("record", {})
    required = payload.get("required", [])
    if isinstance(record, str):
        try:
            record = json.loads(record)
        except json.JSONDecodeError:
            return {"valid": False, "errors": ["invalid_json"]}
    missing = [field for field in required if field not in record]
    return {"valid": not missing, "errors": [f"missing:{field}" for field in missing]}


def _policy_check(payload: dict[str, Any]) -> dict[str, Any]:
    risk = str(payload.get("risk", "low"))
    amount = float(payload.get("amount", 0) or 0)
    requires_approval = risk == "high" or amount > 500
    return {
        "allowed": not requires_approval,
        "requires_approval": requires_approval,
        "reason": "human approval required" if requires_approval else "within agent authority",
    }


default_tools = ToolRegistry()
for tool in [
    Tool("web_search", "Search a supplied, auditable source packet.", _web_search),
    Tool("document_search", "Find relevant passages in a document.", _document_search),
    Tool(
        "ticket_lookup",
        "Load customer and entitlement details for a support ticket.",
        _ticket_lookup,
    ),
    Tool(
        "knowledge_base_search",
        "Find approved support resolution procedures.",
        _knowledge_base_search,
    ),
    Tool("extract_fields", "Extract typed values from unstructured text.", _extract_fields),
    Tool("validate_schema", "Validate a record against required fields.", _validate_schema),
    Tool(
        "policy_check",
        "Check whether an action is allowed or needs human approval.",
        _policy_check,
    ),
]:
    default_tools.register(tool)
