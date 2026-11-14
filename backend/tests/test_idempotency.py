from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.idempotency import begin_idempotent_request, complete_idempotent_request


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def user():
    return SimpleNamespace(id=7, tenant_id="tenant-a")


def test_idempotency_replays_committed_response(db, user):
    record, replay = begin_idempotent_request(
        db, user, "decision-key-001", "invoice.approve", {"invoice_id": 42}
    )
    assert replay is None
    response = {"id": 42, "status": "approved"}
    complete_idempotent_request(record, response)
    db.commit()

    second_record, second_replay = begin_idempotent_request(
        db, user, "decision-key-001", "invoice.approve", {"invoice_id": 42}
    )
    assert second_record.id == record.id
    assert second_replay == response


def test_same_key_cannot_be_reused_for_another_request(db, user):
    record, _ = begin_idempotent_request(
        db, user, "decision-key-002", "invoice.approve", {"invoice_id": 42}
    )
    complete_idempotent_request(record, {"id": 42, "status": "approved"})
    db.commit()

    with pytest.raises(HTTPException) as exc:
        begin_idempotent_request(
            db, user, "decision-key-002", "invoice.reject", {"invoice_id": 42}
        )
    assert exc.value.status_code == 409


def test_idempotency_keys_are_scoped_to_user_and_tenant(db, user):
    record, _ = begin_idempotent_request(
        db, user, "decision-key-003", "invoice.approve", {"invoice_id": 42}
    )
    complete_idempotent_request(record, {"id": 42, "status": "approved"})
    db.commit()

    other_user = SimpleNamespace(id=8, tenant_id="tenant-a")
    _, replay = begin_idempotent_request(
        db, other_user, "decision-key-003", "invoice.approve", {"invoice_id": 42}
    )
    assert replay is None


def test_expired_idempotency_key_can_be_reused(db, user):
    record, _ = begin_idempotent_request(
        db, user, "decision-key-004", "invoice.approve", {"invoice_id": 42}
    )
    original_hash = record.request_hash
    record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    replacement, replay = begin_idempotent_request(
        db, user, "decision-key-004", "invoice.reject", {"invoice_id": 42}
    )
    assert replay is None
    assert replacement.request_hash != original_hash


def test_short_idempotency_key_is_rejected(db, user):
    with pytest.raises(HTTPException) as exc:
        begin_idempotent_request(db, user, "short", "invoice.approve", {"invoice_id": 42})
    assert exc.value.status_code == 400
