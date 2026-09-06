from __future__ import annotations

from typing import Any

from app.connectors.base import BaseConnector, ConnectorError, ConnectorResult, Provenance
from app.connectors.http import SecureHttpClient


class AllowedUrlConnector(BaseConnector):
    name = "url_fetch"
    display_name = "Allowlisted URL Retrieval"
    provenance = Provenance.LIVE

    def __init__(self, *, http: SecureHttpClient | None) -> None:
        self.http = http
        super().__init__(configured=http is not None)

    def execute(self, payload: dict[str, Any]) -> ConnectorResult:
        self._require_configured()
        assert self.http is not None
        url = str(payload.get("url", "")).strip()
        if not url:
            raise ConnectorError("URL retrieval requires a URL")
        response = self.http.request(
            "GET",
            url,
            headers={"Accept": "text/plain, text/html, application/json, application/xml"},
        )
        content_type = response.headers.get("content-type", "").lower()
        if content_type and not any(
            value in content_type for value in ("text/", "json", "xml")
        ):
            raise ConnectorError(f"URL retrieval rejected content type: {content_type}")
        data = {
            "url": response.url,
            "content": response.text,
            "content_type": content_type or None,
            "byte_count": len(response.content),
        }
        return self._result(
            data=data,
            source_url=response.url,
            latency_ms=response.latency_ms,
            content_hash=response.content_hash,
        )

    def _perform_health_check(self) -> None:
        # Arbitrary configured URLs are never probed merely to update a status badge.
        raise ConnectorError("URL retrieval health is established by a successful fetch")
