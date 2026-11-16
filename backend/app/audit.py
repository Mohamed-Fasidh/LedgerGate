import hashlib
import json
from datetime import timezone
from sqlalchemy import text
from app.models import AuditEvent, now_utc

GENESIS_HASH = "0" * 64

def _timestamp(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()

def event_payload(*, tenant_id, actor_user_id, entity_type, entity_id, action, details, created_at):
    return {
        "tenant_id": tenant_id,
        "actor_user_id": actor_user_id,
        "entity_type": entity_type,
        "entity_id": str(entity_id),
        "action": action,
        "details": details or {},
        "created_at": _timestamp(created_at),
    }

def calculate_event_hash(previous_hash: str, payload: dict) -> str:
    canonical = json.dumps(
        {"previous_hash": previous_hash, "event": payload},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def record_event(db, user, entity_type, entity_id, action, details=None):
    # Serialize writers per tenant so two concurrent transactions cannot fork
    # the chain. The transaction-scoped advisory lock is PostgreSQL-specific.
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:tenant_id))"),
            {"tenant_id": user.tenant_id},
        )
    previous = (
        db.query(AuditEvent)
        .filter(AuditEvent.tenant_id == user.tenant_id)
        .order_by(AuditEvent.id.desc())
        .first()
    )
    previous_hash = previous.event_hash if previous else GENESIS_HASH
    created_at = now_utc()
    payload = event_payload(
        tenant_id=user.tenant_id, actor_user_id=user.id,
        entity_type=entity_type, entity_id=entity_id, action=action,
        details=details or {}, created_at=created_at,
    )
    event = AuditEvent(
        tenant_id=user.tenant_id, actor_user_id=user.id,
        entity_type=entity_type, entity_id=str(entity_id),
        action=action, details=details or {}, created_at=created_at,
        previous_hash=previous_hash,
        event_hash=calculate_event_hash(previous_hash, payload),
    )
    db.add(event)
    return event

def verify_tenant_chain(db, tenant_id: str) -> dict:
    rows = (
        db.query(AuditEvent)
        .filter(AuditEvent.tenant_id == tenant_id)
        .order_by(AuditEvent.id.asc())
        .all()
    )
    expected_previous = GENESIS_HASH
    for verified_count, event in enumerate(rows):
        if event.previous_hash != expected_previous:
            return {"valid": False, "verified_events": verified_count, "failed_event_id": event.id, "reason": "previous_hash_mismatch"}
        payload = event_payload(
            tenant_id=event.tenant_id, actor_user_id=event.actor_user_id,
            entity_type=event.entity_type, entity_id=event.entity_id,
            action=event.action, details=event.details, created_at=event.created_at,
        )
        expected_hash = calculate_event_hash(expected_previous, payload)
        if event.event_hash != expected_hash:
            return {"valid": False, "verified_events": verified_count, "failed_event_id": event.id, "reason": "event_hash_mismatch"}
        expected_previous = event.event_hash
    return {"valid": True, "verified_events": len(rows), "head_hash": expected_previous}
