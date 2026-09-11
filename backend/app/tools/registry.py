from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.connectors import (
    ConnectorError,
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


def _sec_company_lookup(payload: dict[str, Any], connectors: ConnectorRegistry) -> ToolExecution:
    snapshot = payload.get("snapshot")
    if isinstance(snapshot, dict):
        started = time.perf_counter()
        evidence = snapshot.get("data")
        if not isinstance(evidence, dict):
            raise ConnectorError("SEC snapshot data must be an object")
        source_url = str(snapshot.get("source_url", "")).strip()
        # The source-packet connector validates that frozen evidence still points
        # at a credential-free HTTPS origin and cannot be mislabeled as LIVE.
        validated = connectors.get("source_packet").execute(
            {
                "query": str(payload.get("query", "SEC company lookup")),
                "sources": [
                    {
                        "title": str(snapshot.get("title", "SEC filing snapshot")),
                        "url": source_url,
                        "snippet": json.dumps(evidence, sort_keys=True),
                        "provenance": "SNAPSHOT",
                    }
                ],
            }
        )
        output = {
            "operation": str(payload.get("operation", "submissions")),
            "cik": str(evidence.get("cik", payload.get("cik", ""))),
            "company": evidence.get("name") or evidence.get("company"),
            "data": evidence,
        }
        return ToolExecution(
            output=output,
            metadata=ConnectorMetadata(
                source_url=validated.metadata.source_url,
                source_urls=validated.metadata.source_urls,
                api_endpoint=validated.metadata.source_url,
                retrieved_at=validated.metadata.retrieved_at,
                content_hash=canonical_hash(evidence),
                connector="sec_edgar_snapshot",
                latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
                provenance=Provenance.SNAPSHOT,
            ),
        )
    result = connectors.get("sec_edgar").execute(payload)
    return ToolExecution(output=result.data, metadata=result.metadata)


def _github_issue_lookup(payload: dict[str, Any], connectors: ConnectorRegistry) -> ToolExecution:
    snapshot = payload.get("snapshot")
    if isinstance(snapshot, dict):
        started = time.perf_counter()
        evidence = snapshot.get("data")
        if not isinstance(evidence, dict):
            raise ConnectorError("GitHub snapshot data must be an object")
        source_url = str(snapshot.get("source_url", "")).strip()
        raw_source_urls = snapshot.get("source_urls", [source_url])
        source_urls = [
            str(url).strip()
            for url in raw_source_urls
            if isinstance(url, str) and str(url).strip()
        ]
        validated = connectors.get("source_packet").execute(
            {
                "query": str(payload.get("query", "GitHub issue lookup")),
                "sources": [
                    {
                        "title": f"{snapshot.get('title', 'GitHub snapshot')}:{index}",
                        "url": url,
                        "snippet": json.dumps(evidence, sort_keys=True),
                        "provenance": "SNAPSHOT",
                    }
                    for index, url in enumerate(source_urls, start=1)
                ],
            }
        )
        operation = str(payload.get("operation", "issue")).lower()
        if operation in {"pull_request", "pull", "pr"}:
            pull = {
                key: evidence.get(key)
                for key in (
                    "number",
                    "title",
                    "state",
                    "draft",
                    "merged",
                    "mergeable_state",
                    "author",
                    "base_ref",
                    "head_sha",
                    "additions",
                    "deletions",
                    "changed_files",
                    "html_url",
                )
            }
            pull["url"] = pull.pop("html_url")
            output = {
                "repository": evidence.get("repository") or payload.get("repository"),
                "operation": "pull_request",
                "pull_request": pull,
                "files": [
                    {"filename": filename}
                    for filename in evidence.get("files", [])
                    if isinstance(filename, str)
                ],
                "summary": {
                    key: evidence.get(key)
                    for key in (
                        "changed_files",
                        "test_files_changed",
                        "security_sensitive_files",
                        "review_count",
                        "approvals",
                        "changes_requested",
                        "check_count",
                        "successful_checks",
                        "failed_checks",
                        "pending_checks",
                    )
                },
                "authenticated": False,
            }
        else:
            issue = {
                "number": evidence.get("number"),
                "title": evidence.get("title"),
                "state": evidence.get("state"),
                "url": evidence.get("html_url") or source_url,
                "created_at": evidence.get("created_at"),
                "updated_at": evidence.get("updated_at"),
                "closed_at": evidence.get("closed_at"),
            }
            output = {
                "repository": evidence.get("repository") or payload.get("repository"),
                "operation": "issue",
                "issues": [issue],
                "source_count": 1,
                "authenticated": False,
            }
        return ToolExecution(
            output=output,
            metadata=ConnectorMetadata(
                source_url=validated.metadata.source_url,
                source_urls=tuple(source_urls),
                api_endpoint=validated.metadata.source_url,
                retrieved_at=validated.metadata.retrieved_at,
                content_hash=str(snapshot.get("content_hash") or canonical_hash(evidence)),
                connector="github_snapshot",
                latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
                provenance=Provenance.SNAPSHOT,
            ),
        )
    result = connectors.get("github").execute(payload)
    return ToolExecution(output=result.data, metadata=result.metadata)


def _web_intelligence(payload: dict[str, Any], connectors: ConnectorRegistry) -> ToolExecution:
    if payload.get("sources"):
        result = connectors.get("source_packet").execute(payload)
    else:
        result = connectors.get("tavily").execute(payload)
    return ToolExecution(output=result.data, metadata=result.metadata)


def _incident_context(payload: dict[str, Any], _: ConnectorRegistry) -> ToolExecution:
    started = time.perf_counter()
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict) or not snapshot:
        raise ConnectorError("Incident response requires a supplied incident snapshot")
    provenance = (
        Provenance.SYNTHETIC
        if str(payload.get("provenance", "SNAPSHOT")).upper() == "SYNTHETIC"
        else Provenance.SNAPSHOT
    )
    return _local_execution(
        dict(snapshot),
        connector="incident_snapshot",
        provenance=provenance,
        started=started,
    )


def _risk_assessment(payload: dict[str, Any], _: ConnectorRegistry) -> ToolExecution:
    started = time.perf_counter()
    raw_factors = payload.get("risk_factors", [])
    factors = [item for item in raw_factors if isinstance(item, dict)]
    weights = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    normalized = [
        {
            "name": str(item.get("name", "Unspecified risk")),
            "severity": str(item.get("severity", "medium")).lower(),
        }
        for item in factors
    ]
    score = max((weights.get(item["severity"], 2) for item in normalized), default=1)
    risk_level = {1: "low", 2: "medium", 3: "high", 4: "critical"}[score]
    evidence_count = sum(
        max(1, int(observation.get("result", {}).get("source_count", 1)))
        for observation in payload.get("observations", [])
        if isinstance(observation, dict) and isinstance(observation.get("result"), dict)
    )
    output = {
        "risk_level": risk_level,
        "risk_score": score,
        "recommendation": "hold" if score >= 3 else "review" if score == 2 else "proceed",
        "factors": normalized,
        "evidence_items_considered": evidence_count,
        "method": "deterministic severity rubric v1",
    }
    return _local_execution(
        output,
        connector="risk_rubric_v1",
        provenance=Provenance.SYNTHETIC,
        started=started,
    )


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


def _knowledge_base_search(payload: dict[str, Any], connectors: ConnectorRegistry) -> ToolExecution:
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
    requires_approval = risk in {"high", "critical"} or amount > approval_threshold
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
    Tool(
        "sec_company_lookup",
        "Retrieve a public SEC company record or its versioned frozen snapshot.",
        _sec_company_lookup,
    ),
    Tool(
        "github_issue_lookup",
        "Retrieve bounded GitHub issue or pull-request evidence, live or from a frozen snapshot.",
        _github_issue_lookup,
    ),
    Tool(
        "web_intelligence",
        "Search Tavily when configured or inspect an explicitly supplied evidence packet.",
        _web_intelligence,
    ),
    Tool(
        "incident_context",
        "Load the bounded incident record supplied to the response workflow.",
        _incident_context,
    ),
    Tool(
        "risk_assessment",
        "Apply the versioned deterministic severity rubric to collected evidence.",
        _risk_assessment,
    ),
]:
    default_tools.register(tool)
