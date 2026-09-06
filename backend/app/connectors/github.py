from __future__ import annotations

import re
from typing import Any

from app.connectors.base import BaseConnector, ConnectorError, ConnectorResult, Provenance
from app.connectors.http import SecureHttpClient

_REPOSITORY_PART = re.compile(r"[A-Za-z0-9_.-]{1,100}")


class GitHubConnector(BaseConnector):
    name = "github"
    display_name = "GitHub Public API"
    provenance = Provenance.LIVE
    api_root = "https://api.github.com"

    def __init__(self, *, token: str | None, http: SecureHttpClient) -> None:
        self.token = (token or "").strip()
        self.http = http
        # Public repositories are accessible without a token, at a lower rate limit.
        super().__init__(configured=True)

    @property
    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "baseline-reliability-platform",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @staticmethod
    def _repository(payload: dict[str, Any]) -> tuple[str, str]:
        owner = str(payload.get("owner", "")).strip()
        repo = str(payload.get("repo", "")).strip()
        repository = str(payload.get("repository", "")).strip()
        if repository and not (owner and repo):
            parts = repository.split("/", maxsplit=1)
            if len(parts) == 2:
                owner, repo = parts
        if not _REPOSITORY_PART.fullmatch(owner) or not _REPOSITORY_PART.fullmatch(repo):
            raise ConnectorError("GitHub requests require a valid owner and repository name")
        return owner, repo

    @staticmethod
    def _normalize_issue(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "number": item.get("number"),
            "title": item.get("title"),
            "state": item.get("state"),
            "url": item.get("html_url"),
            "api_url": item.get("url"),
            "body": item.get("body"),
            "labels": [
                label.get("name")
                for label in item.get("labels", [])
                if isinstance(label, dict) and label.get("name")
            ],
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "is_pull_request": "pull_request" in item,
        }

    def execute(self, payload: dict[str, Any]) -> ConnectorResult:
        owner, repo = self._repository(payload)
        operation = str(payload.get("operation", "issues")).lower()
        if operation == "issue":
            issue_number = payload.get("issue_number")
            if not isinstance(issue_number, int) or issue_number < 1:
                raise ConnectorError("GitHub issue lookup requires a positive issue_number")
            url = f"{self.api_root}/repos/{owner}/{repo}/issues/{issue_number}"
            body, response = self.http.get_json(url, headers=self._headers)
            if not isinstance(body, dict):
                raise ConnectorError("GitHub returned an unexpected issue response")
            issues = [self._normalize_issue(body)]
        elif operation == "issues":
            url = f"{self.api_root}/repos/{owner}/{repo}/issues"
            per_page = min(max(int(payload.get("max_results", 10)), 1), 50)
            state = str(payload.get("state", "open")).lower()
            if state not in {"open", "closed", "all"}:
                raise ConnectorError("GitHub issue state must be open, closed, or all")
            body, response = self.http.get_json(
                url,
                headers=self._headers,
                params={"state": state, "per_page": per_page, "sort": "updated"},
            )
            if not isinstance(body, list):
                raise ConnectorError("GitHub returned an unexpected issues response")
            issues = [self._normalize_issue(item) for item in body if isinstance(item, dict)]
        else:
            raise ConnectorError("GitHub operation must be 'issues' or 'issue'")
        data = {
            "repository": f"{owner}/{repo}",
            "operation": operation,
            "issues": issues,
            "source_count": len(issues),
            "authenticated": bool(self.token),
        }
        source_urls = tuple(str(issue["url"]) for issue in issues if issue.get("url"))
        return self._result(
            data=data,
            source_url=source_urls[0] if source_urls else response.url,
            latency_ms=response.latency_ms,
            content_hash=response.content_hash,
            event_count=len(issues),
            source_urls=source_urls,
            api_endpoint=response.url,
        )

    def _perform_health_check(self) -> None:
        body, _ = self.http.get_json(f"{self.api_root}/rate_limit", headers=self._headers)
        if not isinstance(body, dict) or "resources" not in body:
            raise ConnectorError("GitHub health response was missing rate-limit data")
