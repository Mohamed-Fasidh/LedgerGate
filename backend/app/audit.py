"""Tamper-evident, tenant-scoped audit event creation and verification."""
import hashlib
import json
from datetime import timezone

from sqlalchemy import text

from app.models import AuditEvent, now_utc

GENESIS_HASH = "0" * 64


def _canonical_event(*, tenant_id, actor_user_id, entity_type, entity_id, action, details, created_at):
    timestamp = created_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    payload = {
        "tenant_id": tenant_id,
        "actor_user_id": actor_user_id,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "action": action,
        "details": details or {},
        "created_at": timestamp.astimezone(timezone.utc).isoformat(),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def calculate_event_hash(previous_hash: str, **event_fields) -> str:
    digest = hashlib.sha256()
    digest.update(previous_hash.encode("ascii"))
    digest.update(b"\n")
    digest.update(_canonical_event(**event_fields).encode("utf-8"))
    return digest.hexdigest()


def record_event(db, user, entity_type, entity_id, action, details=None):
    """Append one event while serializing writers for this tenant on PostgreSQL."""
    tenant_id = user.tenant_id
    if db.get_bind().dialect.name == "postgresql":
        # Transaction-scoped advisory lock prevents concurrent writers from
        # reading the same chain head and creating competing branches.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:tenant_id))"), {"tenant_id": tenant_id})

    previous = (
        db.query(AuditEvent)
        .filter(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.id.desc())
        .first()
    )
    previous_hash = previous.event_hash if previous else GENESIS_HASH
    created_at = now_utc()
    event_details = details or {}
    fields = {
        "tenant_id": tenant_id,
        "actor_user_id": user.id,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "action": action,
        "details": event_details,
        "created_at": created_at,
    }
    event = AuditEvent(
        **fields,
        previous_hash=previous_hash,
        event_hash=calculate_event_hash(previous_hash, **fields),
    )
    db.add(event)
    return event


def verify_event_chain(events) -> tuple[bool, str | None]:
    """Verify an ordered collection of events; return first invalid event ID."""
    previous_hash = GENESIS_HASH
    for event in events:
        if event.previous_hash != previous_hash:
            return False, str(event.id)
        expected = calculate_event_hash(
            previous_hash,
            tenant_id=event.tenant_id,
            actor_user_id=event.actor_user_id,
            entity_type=event.entity_type,
            entity_id=event.entity_id,
            action=event.action,
            details=event.details,
            created_at=event.created_at,
        )
        if event.event_hash != expected:
            return False, str(event.id)
        previous_hash = event.event_hash
    return True, None
