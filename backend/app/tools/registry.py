from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.connectors import (
    ConnectorMetadata,
    ConnectorNotConfigured,
    ConnectorRegistry,
    Provenance,
    connector_registry,
)
from app.connectors.base import canonical_hash


@dataclass(frozen=True)
class ToolExecution:
    output: dict[str, Any]
    metadata: ConnectorMetadata


ToolHandler = Callable[[dict[str, Any], ConnectorRegistry], ToolExecution]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    handler: ToolHandler
    mutating: bool = False


class ToolRegistry:
    def __init__(self, connectors: ConnectorRegistry = connector_registry) -> None:
        self._tools: dict[str, Tool] = {}
        self.connectors = connectors

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

    def execute_with_metadata(self, name: str, payload: dict[str, Any]) -> ToolExecution:
        return self.get(name).handler(payload, self.connectors)

    def execute(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Compatibility facade for callers that only need tool output."""
        return self.execute_with_metadata(name, payload).output


def _local_execution(
    output: dict[str, Any],
    *,
    connector: str,
    provenance: Provenance,
    started: float,
    source_url: str | None = None,
) -> ToolExecution:
    return ToolExecution(
        output=output,
        metadata=ConnectorMetadata(
            source_url=source_url,
            retrieved_at=datetime.now(UTC),
            content_hash=canonical_hash(output),
            connector=connector,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
            provenance=provenance,
        ),
    )


def _web_search(payload: dict[str, Any], connectors: ConnectorRegistry) -> ToolExecution:
    requested = str(payload.get("connector", "")).strip().lower()
    if requested:
        name = requested
    elif payload.get("sources"):
        name = "source_packet"
    elif payload.get("cik"):
        name = "sec_edgar"
    elif payload.get("repository") or (payload.get("owner") and payload.get("repo")):
        name = "github"
    else:
        name = "tavily"
    result = connectors.get(name).execute(payload)
    return ToolExecution(output=result.data, metadata=result.metadata)


def _document_search(payload: dict[str, Any], connectors: ConnectorRegistry) -> ToolExecution:
    started = time.perf_counter()
    document = str(payload.get("document", ""))
    source_metadata: ConnectorMetadata | None = None
    if payload.get("url"):
        fetched = connectors.get("url_fetch").execute(payload)
        document = str(fetched.data["content"])
        source_metadata = fetched.metadata
    query = str(payload.get("query", ""))
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", document) if part.strip()]
    terms = {word.lower() for word in re.findall(r"[A-Za-z]{4,}", query)}
    ranked = sorted(
        sentences,
        key=lambda sentence: sum(term in sentence.lower() for term in terms),
        reverse=True,
    )
    output = {"query": query, "chunks": ranked[:3], "matches": min(3, len(ranked))}
    if source_metadata is not None:
        return ToolExecution(output=output, metadata=source_metadata)
    return _local_execution(
        output,
        connector="supplied_document",
        provenance=Provenance.SNAPSHOT,
        started=started,
    )


def _fixture(
    operation: str, payload: dict[str, Any], connectors: ConnectorRegistry
) -> ToolExecution:
    try:
        connector = connectors.get("fixture")
    except KeyError as exc:
        raise ConnectorNotConfigured(
            f"{operation} has no production connector; enable BASELINE_FIXTURE_CONNECTORS only "
            "for tests or deterministic evaluation"
        ) from exc
    result = connector.execute({"operation": operation, **payload})
    return ToolExecution(output=result.data, metadata=result.metadata)


def _ticket_lookup(payload: dict[str, Any], connectors: ConnectorRegistry) -> ToolExecution:
    snapshot = payload.get("snapshot")
    if isinstance(snapshot, dict):
        started = time.perf_counter()
        return _local_execution(
            dict(snapshot),
            connector="support_snapshot",
            provenance=Provenance.SNAPSHOT,
            started=started,
        )
    return _fixture("ticket_lookup", payload, connectors)


def _knowledge_base_search(
    payload: dict[str, Any], connectors: ConnectorRegistry
) -> ToolExecution:
    snapshot = payload.get("snapshot")
    if isinstance(snapshot, dict):
        started = time.perf_counter()
        return _local_execution(
            dict(snapshot),
            connector="support_snapshot",
            provenance=Provenance.SNAPSHOT,
            started=started,
        )
    return _fixture("knowledge_base_search", payload, connectors)


def _extract_fields(payload: dict[str, Any], _: ConnectorRegistry) -> ToolExecution:
    started = time.perf_counter()
    text = str(payload.get("text", ""))
    emails = re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    money = re.findall(r"(?:\$|USD\s?)([\d,]+(?:\.\d{2})?)", text, re.IGNORECASE)
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
    output = {
        "emails": emails,
        "amounts": [float(value.replace(",", "")) for value in money],
        "dates": dates,
        "record_count": max(len(emails), len(money), len(dates), 1 if text else 0),
    }
    return _local_execution(
        output,
        connector="supplied_text",
        provenance=Provenance.SNAPSHOT,
        started=started,
    )


def _validate_schema(payload: dict[str, Any], _: ConnectorRegistry) -> ToolExecution:
    started = time.perf_counter()
    record = payload.get("record", {})
    required = payload.get("required", [])
    if isinstance(record, str):
        try:
            record = json.loads(record)
        except json.JSONDecodeError:
            return _local_execution(
                {"valid": False, "errors": ["invalid_json"]},
                connector="schema_validator",
                provenance=Provenance.SNAPSHOT,
                started=started,
            )
    missing = [field for field in required if field not in record]
    return _local_execution(
        {"valid": not missing, "errors": [f"missing:{field}" for field in missing]},
        connector="schema_validator",
        provenance=Provenance.SNAPSHOT,
        started=started,
    )


def _policy_check(payload: dict[str, Any], _: ConnectorRegistry) -> ToolExecution:
    started = time.perf_counter()
    risk = str(payload.get("risk", "low"))
    amount = float(payload.get("amount", 0) or 0)
    approval_threshold = float(payload.get("approval_threshold_usd", 500) or 0)
    requires_approval = risk == "high" or amount > approval_threshold
    output = {
        "allowed": not requires_approval,
        "requires_approval": requires_approval,
        "reason": "human approval required" if requires_approval else "within agent authority",
    }
    return _local_execution(
        output,
        connector="embedded_policy",
        provenance=Provenance.SYNTHETIC,
        started=started,
    )


default_tools = ToolRegistry()
for tool in [
    Tool(
        "web_search",
        "Search supplied snapshots or configured SEC EDGAR, Tavily, and GitHub sources.",
        _web_search,
    ),
    Tool(
        "document_search",
        "Find relevant passages in supplied or allowlisted content.",
        _document_search,
    ),
    Tool(
        "ticket_lookup",
        "Load deterministic ticket fixtures during explicit fixture evaluation.",
        _ticket_lookup,
    ),
    Tool(
        "knowledge_base_search",
        "Search deterministic knowledge fixtures during explicit fixture evaluation.",
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
