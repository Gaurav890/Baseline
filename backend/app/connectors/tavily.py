from __future__ import annotations

from typing import Any

from app.connectors.base import BaseConnector, ConnectorError, ConnectorResult, Provenance
from app.connectors.http import SecureHttpClient


class TavilyConnector(BaseConnector):
    name = "tavily"
    display_name = "Tavily Web Search"
    provenance = Provenance.LIVE
    endpoint = "https://api.tavily.com/search"

    def __init__(self, *, api_key: str | None, http: SecureHttpClient) -> None:
        self.api_key = (api_key or "").strip()
        self.http = http
        super().__init__(configured=bool(self.api_key))

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _search(self, *, query: str, max_results: int) -> tuple[dict[str, Any], Any]:
        body, response = self.http.post_json(
            self.endpoint,
            headers=self._headers,
            json={
                "query": query,
                "search_depth": "basic",
                "max_results": max_results,
                "include_answer": False,
                "include_raw_content": False,
            },
        )
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise ConnectorError("Tavily returned an unexpected response shape")
        return body, response

    def execute(self, payload: dict[str, Any]) -> ConnectorResult:
        self._require_configured()
        query = str(payload.get("query", "")).strip()
        if not query:
            raise ConnectorError("Tavily search requires a non-empty query")
        max_results = min(max(int(payload.get("max_results", 5)), 1), 10)
        body, response = self._search(query=query, max_results=max_results)
        results = [
            {
                "title": str(item.get("title", "")),
                "url": str(item.get("url", "")),
                "snippet": str(item.get("content", "")),
                "score": item.get("score"),
            }
            for item in body["results"][:max_results]
            if isinstance(item, dict) and item.get("url")
        ]
        data = {
            "query": query,
            "results": results,
            "source_count": len(results),
            "response_time": body.get("response_time"),
        }
        source_urls = tuple(result["url"] for result in results)
        return self._result(
            data=data,
            source_url=source_urls[0] if source_urls else None,
            latency_ms=response.latency_ms,
            content_hash=response.content_hash,
            event_count=len(results),
            source_urls=source_urls,
            api_endpoint=response.url,
        )

    def _perform_health_check(self) -> None:
        body, _ = self._search(query="SEC EDGAR", max_results=1)
        if not body.get("results"):
            raise ConnectorError("Tavily health search returned no results")
