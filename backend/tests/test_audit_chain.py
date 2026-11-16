import pytest
from app.audit import GENESIS_HASH, calculate_event_hash, event_payload
from datetime import datetime, timezone

def test_audit_event_hash_is_deterministic_and_chained():
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    payload = event_payload(
        tenant_id="tenant-a", actor_user_id=7, entity_type="invoice",
        entity_id=42, action="approved", details={"amount": 1200},
        created_at=created,
    )
    first = calculate_event_hash(GENESIS_HASH, payload)
    assert first == calculate_event_hash(GENESIS_HASH, payload)
    assert first != calculate_event_hash("f" * 64, payload)
    changed = dict(payload, action="rejected")
    assert first != calculate_event_hash(GENESIS_HASH, changed)
