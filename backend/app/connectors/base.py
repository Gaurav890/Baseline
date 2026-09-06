from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utcnow() -> datetime:
    return datetime.now(UTC)


class Provenance(StrEnum):
    LIVE = "LIVE"
    SNAPSHOT = "SNAPSHOT"
    SYNTHETIC = "SYNTHETIC"


class HealthStatus(StrEnum):
    UNCONFIGURED = "unconfigured"
    UNCHECKED = "unchecked"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class ConnectorError(RuntimeError):
    """Base error for failures that can be attributed to a data connector."""


class ConnectorNotConfigured(ConnectorError):
    """Raised when a connector cannot run without missing configuration."""


@dataclass(frozen=True)
class ConnectorMetadata:
    source_url: str | None
    retrieved_at: datetime
    content_hash: str
    connector: str
    latency_ms: int
    provenance: Provenance
    source_urls: tuple[str, ...] = ()
    api_endpoint: str | None = None

    def as_trace_attributes(self) -> dict[str, Any]:
        return {
            "source_url": self.source_url,
            "source_urls": list(self.source_urls),
            "api_endpoint": self.api_endpoint,
            "retrieved_at": self.retrieved_at.isoformat(),
            "content_hash": self.content_hash,
            "connector": self.connector,
            "latency_ms": self.latency_ms,
            "provenance": self.provenance.value,
        }


@dataclass(frozen=True)
class ConnectorResult:
    data: dict[str, Any]
    metadata: ConnectorMetadata


@dataclass(frozen=True)
class ConnectorHealth:
    connector: str
    display_name: str
    configured: bool
    status: HealthStatus
    provenance: Provenance
    live: bool
    checked_at: datetime | None = None
    last_successful_sync: datetime | None = None
    latency_ms: int | None = None
    event_count: int = 0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["provenance"] = self.provenance.value
        for key in ("checked_at", "last_successful_sync"):
            timestamp = value[key]
            value[key] = timestamp.isoformat() if timestamp else None
        return value


def canonical_hash(data: Any) -> str:
    encoded = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class BaseConnector(ABC):
    name: str
    display_name: str
    provenance: Provenance
    internal = False

    def __init__(self, *, configured: bool) -> None:
        self.configured = configured
        self._health_lock = threading.Lock()
        self._checked_at: datetime | None = None
        self._last_successful_sync: datetime | None = None
        self._latency_ms: int | None = None
        self._event_count = 0
        self._error: str | None = None

    @abstractmethod
    def execute(self, payload: dict[str, Any]) -> ConnectorResult:
        """Retrieve or produce connector data."""

    @abstractmethod
    def _perform_health_check(self) -> None:
        """Perform a real, minimal check against the configured source."""

    def _require_configured(self) -> None:
        if not self.configured:
            raise ConnectorNotConfigured(f"{self.display_name} is not configured")

    def _result(
        self,
        *,
        data: dict[str, Any],
        source_url: str | None,
        latency_ms: int,
        content_hash: str | None = None,
        event_count: int = 1,
        source_urls: tuple[str, ...] = (),
        api_endpoint: str | None = None,
    ) -> ConnectorResult:
        retrieved_at = utcnow()
        metadata = ConnectorMetadata(
            source_url=source_url,
            retrieved_at=retrieved_at,
            content_hash=content_hash or canonical_hash(data),
            connector=self.name,
            latency_ms=max(0, latency_ms),
            provenance=self.provenance,
            source_urls=source_urls,
            api_endpoint=api_endpoint,
        )
        with self._health_lock:
            self._checked_at = retrieved_at
            self._last_successful_sync = retrieved_at
            self._latency_ms = metadata.latency_ms
            self._event_count += max(0, event_count)
            self._error = None
        return ConnectorResult(data=data, metadata=metadata)

    def health_snapshot(self) -> ConnectorHealth:
        with self._health_lock:
            if not self.configured:
                status = HealthStatus.UNCONFIGURED
            elif self._error is not None:
                status = HealthStatus.UNHEALTHY
            elif self._last_successful_sync is not None:
                status = HealthStatus.HEALTHY
            else:
                status = HealthStatus.UNCHECKED
            return ConnectorHealth(
                connector=self.name,
                display_name=self.display_name,
                configured=self.configured,
                status=status,
                provenance=self.provenance,
                live=(status is HealthStatus.HEALTHY and self.provenance is Provenance.LIVE),
                checked_at=self._checked_at,
                last_successful_sync=self._last_successful_sync,
                latency_ms=self._latency_ms,
                event_count=self._event_count,
                error=self._error,
            )

    def check_health(self) -> ConnectorHealth:
        if not self.configured:
            return self.health_snapshot()
        started = time.perf_counter()
        checked_at = utcnow()
        try:
            self._perform_health_check()
        except Exception as exc:
            with self._health_lock:
                self._checked_at = checked_at
                self._latency_ms = max(0, int((time.perf_counter() - started) * 1000))
                self._error = f"{type(exc).__name__}: {exc}"
        else:
            with self._health_lock:
                self._checked_at = checked_at
                self._last_successful_sync = checked_at
                self._latency_ms = max(0, int((time.perf_counter() - started) * 1000))
                self._error = None
        return self.health_snapshot()

    async def health_check(self) -> ConnectorHealth:
        """Async facade so API health checks do not block the event loop."""
        return await asyncio.to_thread(self.check_health)
