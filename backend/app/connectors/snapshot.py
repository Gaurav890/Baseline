from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

from app.connectors.base import BaseConnector, ConnectorError, ConnectorResult, Provenance


class SourcePacketConnector(BaseConnector):
    """Pass through caller-supplied evidence without representing it as live data."""

    name = "source_packet"
    display_name = "Supplied Source Packet"
    provenance = Provenance.SNAPSHOT
    internal = True

    def __init__(self) -> None:
        super().__init__(configured=True)

    def execute(self, payload: dict[str, Any]) -> ConnectorResult:
        started = time.perf_counter()
        query = str(payload.get("query", ""))
        supplied = payload.get("sources")
        if not isinstance(supplied, list) or not supplied:
            raise ConnectorError("a source packet requires at least one supplied source")
        results: list[dict[str, Any]] = []
        for source in supplied[:50]:
            if not isinstance(source, dict):
                raise ConnectorError("source packet entries must be objects")
            source_url = str(source.get("url", "")).strip()
            if source_url:
                parsed = urlsplit(source_url)
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                ):
                    raise ConnectorError(
                        "source packet URLs must be valid credential-free HTTPS URLs"
                    )
            results.append(
                {
                    "title": str(source.get("title", "")),
                    "url": source_url,
                    "snippet": str(source.get("snippet", "")),
                }
            )
        data = {"query": query, "results": results, "source_count": len(results)}
        source_urls = tuple(result["url"] for result in results if result["url"])
        supplied_provenance = {
            str(source.get("provenance", "")).upper()
            for source in supplied
            if isinstance(source, dict)
        }
        provenance = (
            Provenance.SYNTHETIC
            if supplied_provenance == {Provenance.SYNTHETIC.value}
            else Provenance.SNAPSHOT
        )
        return self._result(
            data=data,
            source_url=source_urls[0] if source_urls else None,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
            event_count=len(results),
            source_urls=source_urls,
            provenance=provenance,
        )

    def _perform_health_check(self) -> None:
        return None
