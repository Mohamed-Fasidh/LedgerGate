"""Database-backed idempotency for state-changing API operations."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import text

from app.models import IdempotencyRecord


def _request_hash(operation: str, payload: dict) -> str:
    canonical = json.dumps(
        {"operation": operation, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def begin_idempotent_request(db, user, key: str | None, operation: str, payload: dict):
    """Return (record, replay_body); serialize same-key requests on PostgreSQL.

    A missing key preserves backward compatibility. A present key is scoped to
    tenant + authenticated user and retained for 24 hours.
    """
    if key is None:
        return None, None
    key = key.strip()
    if len(key) < 8 or len(key) > 128:
        raise HTTPException(400, "Idempotency-Key must contain 8 to 128 characters")

    request_hash = _request_hash(operation, payload)
    if db.get_bind().dialect.name == "postgresql":
        lock_name = f"{user.tenant_id}:{user.id}:{key}"
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
            {"lock_name": lock_name},
        )

    record = (
        db.query(IdempotencyRecord)
        .filter(
            IdempotencyRecord.tenant_id == user.tenant_id,
            IdempotencyRecord.user_id == user.id,
            IdempotencyRecord.key == key,
        )
        .first()
    )
    now = datetime.now(timezone.utc)
    if record and record.expires_at <= now:
        db.delete(record)
        db.flush()
        record = None

    if record:
        if record.request_hash != request_hash:
            raise HTTPException(409, "Idempotency-Key was already used for a different request")
        if record.response_body is None:
            # A committed pending row indicates an interrupted/invalid writer.
            raise HTTPException(409, "A request with this Idempotency-Key is still being processed")
        return record, record.response_body

    record = IdempotencyRecord(
        tenant_id=user.tenant_id,
        user_id=user.id,
        key=key,
        request_hash=request_hash,
        response_status=200,
        response_body=None,
        created_at=now,
        expires_at=now + timedelta(hours=24),
    )
    db.add(record)
    db.flush()
    return record, None


def complete_idempotent_request(record, response_body: dict) -> None:
    if record is not None:
        record.response_status = 200
        record.response_body = response_body
