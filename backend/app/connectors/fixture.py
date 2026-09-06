from __future__ import annotations

import time
from typing import Any

from app.connectors.base import BaseConnector, ConnectorError, ConnectorResult, Provenance


class FixtureConnector(BaseConnector):
    name = "fixture"
    display_name = "Deterministic CI Fixtures"
    provenance = Provenance.SYNTHETIC
    internal = True

    def __init__(self) -> None:
        super().__init__(configured=True)

    def execute(self, payload: dict[str, Any]) -> ConnectorResult:
        started = time.perf_counter()
        operation = str(payload.get("operation", ""))
        if operation == "ticket_lookup":
            ticket_id = str(payload.get("ticket_id", "TKT-DEMO"))
            data = {
                "ticket_id": ticket_id,
                "customer_tier": payload.get("customer_tier", "growth"),
                "status": payload.get("status", "open"),
                "prior_contacts": payload.get("prior_contacts", 1),
                "entitlements": payload.get(
                    "entitlements", ["email_support", "service_credit_review"]
                ),
            }
            source_url = f"fixture://tickets/{ticket_id}"
        elif operation == "knowledge_base_search":
            data = {
                "issue": str(payload.get("issue", "")),
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
            }
            source_url = "fixture://knowledge-base"
        else:
            raise ConnectorError(f"unknown fixture operation: {operation}")
        return self._result(
            data=data,
            source_url=source_url,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
        )

    def _perform_health_check(self) -> None:
        return None
