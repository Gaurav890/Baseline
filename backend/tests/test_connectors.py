from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.connectors import ConnectorError, ConnectorNotConfigured, Provenance
from app.connectors.github import GitHubConnector
from app.connectors.http import ResponseTooLargeError, SecureHttpClient, UnsafeUrlError
from app.connectors.registry import build_connector_registry
from app.connectors.sec_edgar import SecEdgarConnector
from app.connectors.snapshot import SourcePacketConnector
from app.connectors.tavily import TavilyConnector
from app.database import SessionLocal
from app.evals.catalog import GOLDEN_SCENARIOS
from app.models import AgentRun

PUBLIC_IP = "93.184.216.34"


def _client(
    handler: httpx.MockTransport,
    *,
    domains: tuple[str, ...],
    max_bytes: int = 2_000_000,
    retries: int = 0,
    sleep=lambda _: None,
) -> SecureHttpClient:
    return SecureHttpClient(
        allowed_domains=domains,
        max_response_bytes=max_bytes,
        retries=retries,
        client=httpx.Client(transport=handler),
        resolve_host=lambda _: [PUBLIC_IP],
        sleep=sleep,
    )


def test_secure_http_rejects_disallowed_and_private_network_targets() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    client = _client(transport, domains=("api.example.com",))
    with pytest.raises(UnsafeUrlError, match="not allowlisted"):
        client.get_json("https://localhost/internal")

    private_client = SecureHttpClient(
        allowed_domains=("api.example.com",),
        client=httpx.Client(transport=transport),
        resolve_host=lambda _: ["127.0.0.1"],
    )
    with pytest.raises(UnsafeUrlError, match="non-public"):
        private_client.get_json("https://api.example.com/data")


def test_secure_http_enforces_size_and_retries_with_backoff() -> None:
    oversized = _client(
        httpx.MockTransport(lambda request: httpx.Response(200, content=b"12345")),
        domains=("api.example.com",),
        max_bytes=4,
    )
    with pytest.raises(ResponseTooLargeError):
        oversized.get_json("https://api.example.com/data")

    attempts = 0
    delays: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"ok": True})

    retrying = _client(
        httpx.MockTransport(handler),
        domains=("api.example.com",),
        retries=2,
        sleep=delays.append,
    )
    body, _ = retrying.get_json("https://api.example.com/data")
    assert body == {"ok": True}
    assert attempts == 3
    assert delays == [0.2, 0.4]


def test_sec_edgar_supports_submissions_and_companyfacts() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["user-agent"] == "Baseline ops@example.com"
        return httpx.Response(200, json={"cik": "320193", "name": "Apple Inc."})

    connector = SecEdgarConnector(
        user_agent="Baseline ops@example.com",
        http=_client(httpx.MockTransport(handler), domains=("data.sec.gov",)),
    )
    submissions = connector.execute({"cik": "320193"})
    facts = connector.execute({"cik": 320193, "operation": "companyfacts"})
    assert paths == [
        "/submissions/CIK0000320193.json",
        "/api/xbrl/companyfacts/CIK0000320193.json",
    ]
    assert submissions.metadata.provenance is Provenance.LIVE
    assert facts.data["operation"] == "companyfacts"
    assert connector.health_snapshot().live is True


def test_tavily_and_github_normalize_evidence_and_provenance() -> None:
    def tavily_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tavily-secret"
        assert json.loads(request.content)["query"] == "reliability"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Primary source",
                        "url": "https://docs.example.com/reliability",
                        "content": "Evidence",
                        "score": 0.9,
                    }
                ]
            },
        )

    tavily = TavilyConnector(
        api_key="tavily-secret",
        http=_client(httpx.MockTransport(tavily_handler), domains=("api.tavily.com",)),
    )
    search = tavily.execute({"query": "reliability"})
    assert search.metadata.source_url == "https://docs.example.com/reliability"
    assert search.metadata.api_endpoint == "https://api.tavily.com/search"
    assert len(search.metadata.content_hash) == 64

    def github_handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        assert request.url.params["state"] == "open"
        return httpx.Response(
            200,
            json=[
                {
                    "number": 1,
                    "title": "Bug",
                    "state": "open",
                    "html_url": "https://github.com/example/repo/issues/1",
                    "url": "https://api.github.com/repos/example/repo/issues/1",
                    "labels": [{"name": "bug"}],
                }
            ],
        )

    github = GitHubConnector(
        token=None,
        http=_client(httpx.MockTransport(github_handler), domains=("api.github.com",)),
    )
    issues = github.execute({"repository": "example/repo"})
    assert issues.metadata.source_url == "https://github.com/example/repo/issues/1"
    assert issues.data["authenticated"] is False
    assert issues.data["issues"][0]["labels"] == ["bug"]


def test_github_pull_request_collects_read_only_release_evidence() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["authorization"] == "Bearer github-secret"
        if request.url.path.endswith("/pulls/7"):
            return httpx.Response(
                200,
                json={
                    "number": 7,
                    "title": "Harden authentication",
                    "state": "open",
                    "draft": False,
                    "merged": False,
                    "mergeable": True,
                    "mergeable_state": "unstable",
                    "user": {"login": "contributor"},
                    "head": {"sha": "a" * 40},
                    "base": {"ref": "main"},
                    "html_url": "https://github.com/example/repo/pull/7",
                    "url": "https://api.github.com/repos/example/repo/pulls/7",
                    "changed_files": 2,
                },
            )
        if request.url.path.endswith("/pulls/7/files"):
            return httpx.Response(
                200,
                json=[
                    {"filename": "src/auth/token.py", "status": "modified"},
                    {"filename": "tests/test_token.py", "status": "added"},
                ],
            )
        if request.url.path.endswith("/pulls/7/reviews"):
            return httpx.Response(
                200,
                json=[
                    {
                        "state": "APPROVED",
                        "user": {"login": "reviewer"},
                        "submitted_at": "2026-01-01T00:00:00Z",
                    }
                ],
            )
        if request.url.path.endswith(f"/commits/{'a' * 40}/check-runs"):
            return httpx.Response(
                200,
                json={
                    "check_runs": [
                        {
                            "name": "tests",
                            "status": "completed",
                            "conclusion": "failure",
                            "details_url": "https://github.com/example/repo/actions/runs/1",
                        }
                    ]
                },
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    github = GitHubConnector(
        token="github-secret",
        http=_client(httpx.MockTransport(handler), domains=("api.github.com",)),
    )
    result = github.execute(
        {"repository": "example/repo", "operation": "pull_request", "pull_number": 7}
    )

    assert paths == [
        "/repos/example/repo/pulls/7",
        "/repos/example/repo/pulls/7/files",
        "/repos/example/repo/pulls/7/reviews",
        f"/repos/example/repo/commits/{'a' * 40}/check-runs",
    ]
    assert result.data["authenticated"] is True
    assert result.data["summary"] == {
        "file_count": 2,
        "test_files_changed": True,
        "security_sensitive_files": ["src/auth/token.py"],
        "review_count": 1,
        "approvals": 1,
        "changes_requested": 0,
        "check_count": 1,
        "successful_checks": 0,
        "failed_checks": ["tests"],
        "pending_checks": [],
    }
    assert result.metadata.provenance is Provenance.LIVE
    assert len(result.metadata.content_hash) == 64


def test_snapshot_validates_source_urls_and_never_claims_live() -> None:
    connector = SourcePacketConnector()
    result = connector.execute(
        {
            "query": "evidence",
            "sources": [
                {
                    "title": "Frozen evidence",
                    "url": "https://example.com/evidence",
                    "snippet": "Known at snapshot time.",
                }
            ],
        }
    )
    assert result.metadata.provenance is Provenance.SNAPSHOT
    assert connector.health_snapshot().live is False
    with pytest.raises(ConnectorError, match="HTTPS"):
        connector.execute(
            {
                "sources": [
                    {"title": "unsafe", "url": "file:///etc/passwd", "snippet": ""}
                ]
            }
        )


def test_registry_only_enables_fixtures_explicitly() -> None:
    production_like = build_connector_registry(
        Settings(environment="development", fixture_connectors=False)
    )
    with pytest.raises(KeyError):
        production_like.get("fixture")
    assert production_like.get("tavily").configured is False
    with pytest.raises(ConnectorNotConfigured):
        production_like.get("tavily").execute({"query": "no silent fallback"})

    fixture_registry = build_connector_registry(
        Settings(environment="test", fixture_connectors=True)
    )
    assert fixture_registry.get("fixture").provenance is Provenance.SYNTHETIC

    non_test_registry = build_connector_registry(
        Settings(environment="development", fixture_connectors=True)
    )
    with pytest.raises(KeyError):
        non_test_registry.get("fixture")


def test_agent_tool_spans_capture_complete_provenance(client) -> None:
    scenario = GOLDEN_SCENARIOS[0]
    response = client.post(
        "/api/runs",
        json={
            "task": scenario.task,
            "agent_type": scenario.category,
            "provider": "mock",
            "context": scenario.context,
            "scenario_id": scenario.id,
        },
    )
    assert response.status_code == 201
    tool_spans = [span for span in response.json()["spans"] if span["kind"] == "tool"]
    assert tool_spans
    for span in tool_spans:
        attributes = span["attributes"]
        assert {
            "source_url",
            "retrieved_at",
            "content_hash",
            "connector",
            "latency_ms",
            "provenance",
        } <= attributes.keys()
        assert len(attributes["content_hash"]) == 64
        assert attributes["provenance"] in {"LIVE", "SNAPSHOT", "SYNTHETIC"}
    with SessionLocal() as db:
        run = db.get(AgentRun, response.json()["id"])
        assert run is not None
        db.delete(run)
        db.commit()
