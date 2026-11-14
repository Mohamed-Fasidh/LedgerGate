from datetime import datetime, timezone
from types import SimpleNamespace

from app.audit import GENESIS_HASH, calculate_event_hash, verify_event_chain


def _event(event_id, previous_hash, action, details, created_at):
    fields = {
        "tenant_id": "tenant-a",
        "actor_user_id": 7,
        "entity_type": "invoice",
        "entity_id": "42",
        "action": action,
        "details": details,
        "created_at": created_at,
    }
    return SimpleNamespace(
        id=event_id,
        **fields,
        previous_hash=previous_hash,
        event_hash=calculate_event_hash(previous_hash, **fields),
    )


def test_hash_chain_verifies_and_detects_modified_event():
    timestamp = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
    first = _event(1, GENESIS_HASH, "created", {"amount": 100}, timestamp)
    second = _event(2, first.event_hash, "approved", {"reviewer": 7}, timestamp)

    assert verify_event_chain([first, second]) == (True, None)

    second.details = {"reviewer": 8}
    valid, invalid_id = verify_event_chain([first, second])
    assert valid is False
    assert invalid_id == "2"


def test_hash_chain_detects_removed_or_reordered_link():
    timestamp = datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc)
    first = _event(1, GENESIS_HASH, "created", {}, timestamp)
    second = _event(2, first.event_hash, "approved", {}, timestamp)

    valid, invalid_id = verify_event_chain([second])
    assert valid is False
    assert invalid_id == "2"
