from __future__ import annotations

import hashlib
import ipaddress
import socket
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.connectors.base import ConnectorError


class UnsafeUrlError(ConnectorError):
    """Raised before a request that violates the connector network policy."""


class ResponseTooLargeError(ConnectorError):
    """Raised when a response exceeds the configured byte ceiling."""


class UpstreamResponseError(ConnectorError):
    """Raised when an upstream returns a non-successful response."""


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status_code: int
    headers: Mapping[str, str]
    content: bytes
    latency_ms: int
    content_hash: str

    def json(self) -> Any:
        try:
            return httpx.Response(self.status_code, content=self.content).json()
        except ValueError as exc:
            raise UpstreamResponseError(f"{self.url} returned invalid JSON") from exc

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def _resolve_public_addresses(host: str) -> Iterable[str]:
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"could not resolve connector host: {host}") from exc
    return {record[4][0] for record in records}


class SecureHttpClient:
    """Bounded HTTP client for known public APIs.

    The caller must supply a narrow host allowlist. DNS is checked before every
    attempt, redirects are rejected, response bytes are counted after decoding,
    and only HTTPS on the default port is accepted.
    """

    RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __init__(
        self,
        *,
        allowed_domains: Iterable[str],
        timeout_seconds: float = 8.0,
        max_response_bytes: int = 2_000_000,
        retries: int = 2,
        backoff_seconds: float = 0.2,
        client: httpx.Client | None = None,
        resolve_host: Callable[[str], Iterable[str]] = _resolve_public_addresses,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        domains = {
            domain.strip().rstrip(".").lower()
            for domain in allowed_domains
            if domain.strip()
        }
        if not domains:
            raise ValueError("SecureHttpClient requires at least one allowed domain")
        if timeout_seconds <= 0 or max_response_bytes <= 0 or retries < 0:
            raise ValueError("HTTP limits must be positive and retries cannot be negative")
        self.allowed_domains = frozenset(domains)
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.retries = retries
        self.backoff_seconds = max(0.0, backoff_seconds)
        self._resolve_host = resolve_host
        self._sleep = sleep
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )

    def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        if parsed.scheme != "https" or not host:
            raise UnsafeUrlError("connector URLs must use HTTPS and include a hostname")
        if parsed.username or parsed.password:
            raise UnsafeUrlError("credentials are not permitted in connector URLs")
        if parsed.port not in (None, 443):
            raise UnsafeUrlError("non-standard connector ports are not permitted")
        if host not in self.allowed_domains:
            raise UnsafeUrlError(f"connector host is not allowlisted: {host}")
        addresses = tuple(self._resolve_host(host))
        if not addresses:
            raise UnsafeUrlError(f"connector host has no resolved addresses: {host}")
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise UnsafeUrlError(f"connector host resolved to a non-public address: {host}")

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> HttpResponse:
        method = method.upper()
        if method not in {"GET", "POST"}:
            raise ValueError("connector HTTP client only permits GET and POST")
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._validate_url(url)
            started = time.perf_counter()
            try:
                with self._client.stream(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json,
                    timeout=self.timeout_seconds,
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise UnsafeUrlError("connector redirects are not followed")
                    if response.status_code in self.RETRYABLE_STATUS_CODES:
                        raise UpstreamResponseError(
                            f"{url} returned retryable HTTP {response.status_code}"
                        )
                    if response.status_code < 200 or response.status_code >= 300:
                        raise UpstreamResponseError(
                            f"{url} returned HTTP {response.status_code}"
                        )
                    declared_size = response.headers.get("content-length")
                    if declared_size:
                        try:
                            is_oversized = int(declared_size) > self.max_response_bytes
                        except ValueError:
                            is_oversized = False
                        if is_oversized:
                            raise ResponseTooLargeError(
                                f"response exceeds {self.max_response_bytes} bytes"
                            )
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > self.max_response_bytes:
                            raise ResponseTooLargeError(
                                f"response exceeds {self.max_response_bytes} bytes"
                            )
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    return HttpResponse(
                        url=str(response.url),
                        status_code=response.status_code,
                        headers=response.headers,
                        content=content,
                        latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
                        content_hash=hashlib.sha256(content).hexdigest(),
                    )
            except (httpx.TimeoutException, httpx.NetworkError, UpstreamResponseError) as exc:
                last_error = exc
                if attempt == self.retries:
                    break
                self._sleep(self.backoff_seconds * (2**attempt))
        assert last_error is not None
        if isinstance(last_error, ConnectorError):
            raise last_error
        raise ConnectorError(f"request to {url} failed: {last_error}") from last_error

    def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> tuple[Any, HttpResponse]:
        response = self.request("GET", url, headers=headers, params=params)
        return response.json(), response

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: Any = None,
    ) -> tuple[Any, HttpResponse]:
        response = self.request("POST", url, headers=headers, json=json)
        return response.json(), response
