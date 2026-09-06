from __future__ import annotations

import re
import threading
import time
from typing import Any

from app.connectors.base import BaseConnector, ConnectorError, ConnectorResult, Provenance
from app.connectors.http import SecureHttpClient


class SecEdgarConnector(BaseConnector):
    name = "sec_edgar"
    display_name = "SEC EDGAR"
    provenance = Provenance.LIVE

    def __init__(
        self,
        *,
        user_agent: str | None,
        http: SecureHttpClient,
        requests_per_second: float = 4.0,
    ) -> None:
        if not 0 < requests_per_second <= 10:
            raise ValueError("SEC requests_per_second must be greater than 0 and at most 10")
        self.user_agent = (user_agent or "").strip()
        self.http = http
        self._request_interval = 1 / requests_per_second
        self._request_lock = threading.Lock()
        self._last_request_started = 0.0
        super().__init__(configured=bool(self.user_agent))

    def _get_json(self, url: str) -> tuple[Any, Any]:
        # Render runs one API and one worker by default. Four requests/second per
        # process keeps their combined ceiling below the SEC's published 10 rps.
        with self._request_lock:
            elapsed = time.monotonic() - self._last_request_started
            if elapsed < self._request_interval:
                time.sleep(self._request_interval - elapsed)
            self._last_request_started = time.monotonic()
        return self.http.get_json(url, headers=self._headers)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": self.user_agent,
        }

    @staticmethod
    def _cik(value: Any) -> str:
        raw = str(value or "").strip()
        if not re.fullmatch(r"\d{1,10}", raw):
            raise ConnectorError("SEC EDGAR requests require a numeric CIK of at most 10 digits")
        return raw.zfill(10)

    def execute(self, payload: dict[str, Any]) -> ConnectorResult:
        self._require_configured()
        cik = self._cik(payload.get("cik"))
        operation = str(payload.get("operation", "submissions")).lower()
        if operation == "submissions":
            url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        elif operation in {"companyfacts", "xbrl"}:
            url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        else:
            raise ConnectorError("SEC EDGAR operation must be 'submissions' or 'companyfacts'")
        body, response = self._get_json(url)
        if not isinstance(body, dict):
            raise ConnectorError("SEC EDGAR returned an unexpected response shape")
        data = {
            "operation": "companyfacts" if operation in {"companyfacts", "xbrl"} else operation,
            "cik": cik,
            "company": body.get("name") or body.get("entityName"),
            "data": body,
        }
        return self._result(
            data=data,
            source_url=response.url,
            latency_ms=response.latency_ms,
            content_hash=response.content_hash,
            event_count=1,
        )

    def _perform_health_check(self) -> None:
        # Apple is a stable public issuer and keeps the no-key check small enough for readiness UI.
        url = "https://data.sec.gov/submissions/CIK0000320193.json"
        body, _ = self._get_json(url)
        if not isinstance(body, dict) or not body.get("cik"):
            raise ConnectorError("SEC EDGAR health response was missing an issuer CIK")
