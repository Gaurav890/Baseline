from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors import ConnectorHealth, connector_registry
from app.models import Integration

_runtime_id = str(uuid4())


def _persist_snapshot(
    db: Session,
    health: ConnectorHealth,
    *,
    verified: bool,
) -> Integration:
    record = db.scalar(select(Integration).where(Integration.name == health.connector))
    if record is None:
        record = Integration(name=health.connector, display_name=health.display_name)
        db.add(record)

    record.display_name = health.display_name
    record.configured = health.configured
    record.health_status = health.status.value
    record.last_checked_at = health.checked_at
    record.last_error = health.error
    record.latency_ms = health.latency_ms

    runtime_metadata = dict(record.metadata_json or {})
    previous_runtime_count = (
        int(runtime_metadata.get("runtime_event_count", 0))
        if runtime_metadata.get("runtime_id") == _runtime_id
        else 0
    )
    delta = max(0, health.event_count - previous_runtime_count)
    record.event_count = max(0, record.event_count or 0) + delta
    runtime_metadata.update(
        {"runtime_id": _runtime_id, "runtime_event_count": health.event_count}
    )
    record.metadata_json = runtime_metadata

    if not health.configured:
        record.live = False
        record.provenance = None
    elif health.status.value != "healthy":
        # Keep the historical successful-sync timestamp and provenance, but
        # never expose a stale source as currently live.
        record.live = False
    elif health.last_successful_sync is not None and (verified or health.status.value == "healthy"):
        # A successful check or retrieval is the only path that can assert LIVE.
        record.last_success_at = health.last_successful_sync
        record.provenance = health.provenance.value
        record.live = bool(health.live)
    elif record.last_success_at is None:
        record.provenance = None
        record.live = False
    return record


def sync_integration_catalog(db: Session) -> list[Integration]:
    records = [
        _persist_snapshot(db, connector.health_snapshot(), verified=False)
        for connector in connector_registry.list()
    ]
    db.commit()
    for record in records:
        db.refresh(record)
    return sorted(records, key=lambda item: item.display_name.lower())


async def check_integration(name: str, db: Session) -> Integration:
    connector = connector_registry.get(name)
    health = await connector.health_check()
    record = _persist_snapshot(db, health, verified=health.status.value == "healthy")
    db.commit()
    db.refresh(record)
    return record
