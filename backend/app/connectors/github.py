from __future__ import annotations

import re
from typing import Any

from app.connectors.base import (
    BaseConnector,
    ConnectorError,
    ConnectorResult,
    Provenance,
    canonical_hash,
)
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

    @staticmethod
    def _positive_number(payload: dict[str, Any], *keys: str) -> int:
        value = next((payload.get(key) for key in keys if payload.get(key) is not None), None)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ConnectorError("GitHub lookup requires a positive issue or pull request number")
        return value

    @staticmethod
    def _require_object(value: Any, label: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ConnectorError(f"GitHub returned an unexpected {label} response")
        return value

    @staticmethod
    def _require_list(value: Any, label: str) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ConnectorError(f"GitHub returned an unexpected {label} response")
        return [item for item in value if isinstance(item, dict)]

    @staticmethod
    def _normalize_pull_request(item: dict[str, Any]) -> dict[str, Any]:
        head = item.get("head") if isinstance(item.get("head"), dict) else {}
        base = item.get("base") if isinstance(item.get("base"), dict) else {}
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        return {
            "number": item.get("number"),
            "title": item.get("title"),
            "state": item.get("state"),
            "draft": bool(item.get("draft")),
            "merged": bool(item.get("merged")),
            "mergeable": item.get("mergeable"),
            "mergeable_state": item.get("mergeable_state"),
            "author": user.get("login"),
            "base_ref": base.get("ref"),
            "head_sha": head.get("sha"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "closed_at": item.get("closed_at"),
            "merged_at": item.get("merged_at"),
            "additions": item.get("additions"),
            "deletions": item.get("deletions"),
            "changed_files": item.get("changed_files"),
            "commits": item.get("commits"),
            "comments": item.get("comments"),
            "review_comments": item.get("review_comments"),
            "url": item.get("html_url"),
            "api_url": item.get("url"),
        }

    @staticmethod
    def _normalize_file(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "filename": item.get("filename"),
            "status": item.get("status"),
            "additions": item.get("additions"),
            "deletions": item.get("deletions"),
            "changes": item.get("changes"),
            "url": item.get("blob_url"),
        }

    @staticmethod
    def _normalize_review(item: dict[str, Any]) -> dict[str, Any]:
        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        return {
            "state": str(item.get("state", "")).upper(),
            "reviewer": user.get("login"),
            "submitted_at": item.get("submitted_at"),
            "commit_id": item.get("commit_id"),
            "url": item.get("html_url"),
        }

    @staticmethod
    def _normalize_check(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": item.get("name"),
            "status": item.get("status"),
            "conclusion": item.get("conclusion"),
            "started_at": item.get("started_at"),
            "completed_at": item.get("completed_at"),
            "url": item.get("details_url"),
        }

    @staticmethod
    def _pull_request_summary(
        files: list[dict[str, Any]],
        reviews: list[dict[str, Any]],
        checks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        latest_review_by_user: dict[str, str] = {}
        for review in reviews:
            reviewer = str(review.get("reviewer") or "").strip()
            state = str(review.get("state") or "").upper()
            if reviewer and state:
                latest_review_by_user[reviewer] = state
        failed_checks = [
            str(check.get("name"))
            for check in checks
            if check.get("status") == "completed"
            and check.get("conclusion") not in {"success", "neutral", "skipped"}
        ]
        pending_checks = [
            str(check.get("name"))
            for check in checks
            if check.get("status") != "completed" or check.get("conclusion") is None
        ]
        security_pattern = re.compile(
            r"(^|[/_.-])(auth|oauth|security|permission|secret|crypto|url|network)([/_.-]|$)",
            re.IGNORECASE,
        )
        filenames = [str(item.get("filename")) for item in files if item.get("filename")]
        return {
            "file_count": len(files),
            "test_files_changed": any(
                name.startswith(("test/", "tests/"))
                or "/test" in name
                or name.endswith(("_test.py", ".test.ts", ".test.tsx"))
                for name in filenames
            ),
            "security_sensitive_files": [
                name for name in filenames if security_pattern.search(name)
            ],
            "review_count": len(reviews),
            "approvals": sum(state == "APPROVED" for state in latest_review_by_user.values()),
            "changes_requested": sum(
                state == "CHANGES_REQUESTED" for state in latest_review_by_user.values()
            ),
            "check_count": len(checks),
            "successful_checks": sum(
                check.get("status") == "completed" and check.get("conclusion") == "success"
                for check in checks
            ),
            "failed_checks": failed_checks,
            "pending_checks": pending_checks,
        }

    def _pull_request(self, owner: str, repo: str, payload: dict[str, Any]) -> ConnectorResult:
        number = self._positive_number(payload, "pull_number", "issue_number")
        root = f"{self.api_root}/repos/{owner}/{repo}"
        pull_url = f"{root}/pulls/{number}"
        pull_body, pull_response = self.http.get_json(pull_url, headers=self._headers)
        pull = self._normalize_pull_request(self._require_object(pull_body, "pull request"))
        head_sha = str(pull.get("head_sha") or "").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha):
            raise ConnectorError("GitHub pull request response was missing a valid head SHA")

        files_body, files_response = self.http.get_json(
            f"{pull_url}/files", headers=self._headers, params={"per_page": 100}
        )
        reviews_body, reviews_response = self.http.get_json(
            f"{pull_url}/reviews", headers=self._headers, params={"per_page": 100}
        )
        checks_url = f"{root}/commits/{head_sha}/check-runs"
        checks_body, checks_response = self.http.get_json(
            checks_url, headers=self._headers, params={"per_page": 100}
        )
        checks_packet = self._require_object(checks_body, "check runs")
        files = [
            self._normalize_file(item)
            for item in self._require_list(files_body, "pull request files")
        ]
        reviews = [
            self._normalize_review(item)
            for item in self._require_list(reviews_body, "pull request reviews")
        ]
        checks = [
            self._normalize_check(item)
            for item in self._require_list(checks_packet.get("check_runs"), "check runs")
        ]
        data = {
            "repository": f"{owner}/{repo}",
            "operation": "pull_request",
            "pull_request": pull,
            "files": files,
            "reviews": reviews,
            "checks": checks,
            "summary": self._pull_request_summary(files, reviews, checks),
            "authenticated": bool(self.token),
        }
        source_urls = tuple(
            dict.fromkeys(
                str(url)
                for url in [
                    pull.get("url"),
                    *[item.get("url") for item in checks],
                ]
                if url
            )
        )
        latency_ms = sum(
            response.latency_ms
            for response in (
                pull_response,
                files_response,
                reviews_response,
                checks_response,
            )
        )
        return self._result(
            data=data,
            source_url=str(pull.get("url") or pull_response.url),
            source_urls=source_urls,
            api_endpoint=pull_response.url,
            latency_ms=latency_ms,
            content_hash=canonical_hash(data),
            event_count=1 + len(files) + len(reviews) + len(checks),
        )

    def execute(self, payload: dict[str, Any]) -> ConnectorResult:
        owner, repo = self._repository(payload)
        operation = str(payload.get("operation", "issues")).lower()
        if operation in {"pull_request", "pull", "pr"}:
            return self._pull_request(owner, repo, payload)
        if operation == "issue":
            issue_number = self._positive_number(payload, "issue_number")
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
            raise ConnectorError(
                "GitHub operation must be 'issues', 'issue', or 'pull_request'"
            )
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
