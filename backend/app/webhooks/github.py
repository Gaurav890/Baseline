from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.config import settings

_HEAD_SHA = re.compile(r"^[0-9a-f]{40,64}$")
_SUPPORTED_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review"}


@dataclass(frozen=True)
class GitHubPullRequestEvent:
    repository: str
    pull_number: int
    head_sha: str
    action: str
    installation_id: int | None
    html_url: str

    def metadata(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "pull_number": self.pull_number,
            "head_sha": self.head_sha,
            "action": self.action,
            "installation_id": self.installation_id,
            "html_url": self.html_url,
        }


def verify_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    supplied = signature_header.removeprefix("sha256=")
    if len(supplied) != 64 or any(char not in "0123456789abcdef" for char in supplied):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied)


def parse_pull_request_event(payload: dict[str, Any]) -> GitHubPullRequestEvent | None:
    action = payload.get("action")
    if action not in _SUPPORTED_ACTIONS:
        return None
    repository = payload.get("repository")
    pull_request = payload.get("pull_request")
    number = payload.get("number")
    if not isinstance(repository, dict) or not isinstance(pull_request, dict):
        raise ValueError("GitHub pull_request payload is missing required objects")
    full_name = repository.get("full_name")
    head = pull_request.get("head")
    html_url = pull_request.get("html_url")
    if (
        not isinstance(full_name, str)
        or "/" not in full_name
        or not isinstance(number, int)
        or number <= 0
        or not isinstance(head, dict)
        or not isinstance(head.get("sha"), str)
        or not _HEAD_SHA.fullmatch(head["sha"])
        or not isinstance(html_url, str)
        or not html_url.startswith("https://github.com/")
    ):
        raise ValueError("GitHub pull_request payload contains invalid fields")
    installation = payload.get("installation")
    installation_id = installation.get("id") if isinstance(installation, dict) else None
    if installation_id is not None and not isinstance(installation_id, int):
        raise ValueError("GitHub installation id must be an integer")
    return GitHubPullRequestEvent(
        repository=full_name.lower(),
        pull_number=number,
        head_sha=head["sha"],
        action=action,
        installation_id=installation_id,
        html_url=html_url,
    )


def repository_allowed(repository: str, allowlist: tuple[str, ...]) -> bool:
    normalized = repository.lower()
    return normalized in {item.lower() for item in allowlist}


async def publish_check_run(
    *,
    repository: str,
    head_sha: str,
    installation_id: int | None,
    job_id: str,
    success_rate: float,
    regression_detected: bool,
) -> dict[str, Any]:
    """Publish a completed check when the least-privilege integration is enabled."""
    if not settings.github_checks_enabled:
        return {"published": False, "reason": "checks_disabled"}
    if not repository_allowed(repository, settings.github_repository_allowlist):
        return {"published": False, "reason": "repository_not_allowed"}

    conclusion = "failure" if regression_detected else "success"
    summary = (
        f"Regression detected at {success_rate:.1%} pass rate."
        if regression_detected
        else f"Reliability evaluation passed at {success_rate:.1%}."
    )
    safe_repository = "/".join(quote(part, safe="") for part in repository.split("/", 1))
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False, trust_env=False) as client:
        token = settings.github_token
        if settings.github_app_id and settings.github_private_key:
            if installation_id is None:
                return {"published": False, "reason": "installation_id_missing"}
            token = await _installation_access_token(client, installation_id)
        if not token:
            return {"published": False, "reason": "token_unconfigured"}
        response = await client.post(
            f"https://api.github.com/repos/{safe_repository}/check-runs",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "baseline-reliability-platform",
            },
            json={
                "name": settings.github_check_name,
                "head_sha": head_sha,
                "status": "completed",
                "conclusion": conclusion,
                "external_id": job_id,
                "output": {"title": settings.github_check_name, "summary": summary},
            },
        )
    if response.status_code not in {200, 201}:
        raise RuntimeError(f"GitHub Checks API returned HTTP {response.status_code}")
    response_payload = response.json()
    return {
        "published": True,
        "check_run_id": response_payload.get("id"),
        "url": response_payload.get("html_url"),
        "conclusion": conclusion,
    }


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _github_app_jwt() -> str:
    assert settings.github_app_id is not None
    assert settings.github_private_key is not None
    now = int(time.time())
    header = _base64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _base64url(
        json.dumps(
            {"iat": now - 60, "exp": now + 9 * 60, "iss": settings.github_app_id},
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    private_key = serialization.load_pem_private_key(
        settings.github_private_key.encode(), password=None
    )
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_base64url(signature)}"


async def _installation_access_token(client: httpx.AsyncClient, installation_id: int) -> str:
    response = await client.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {_github_app_jwt()}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "baseline-reliability-platform",
        },
    )
    if response.status_code != 201:
        raise RuntimeError(
            f"GitHub installation-token exchange returned HTTP {response.status_code}"
        )
    payload = response.json()
    token = payload.get("token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("GitHub installation-token exchange returned no token")
    return token
