from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.main import _record_registration_attempt, _registration_hash, _registration_lock_key
from app.models import RegistrationAttempt


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    RegistrationAttempt.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_registration_allows_five_attempts_per_email_then_throttles(db):
    now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
    for _ in range(5):
        assert _record_registration_attempt(db, "Person@example.com", "192.0.2.10", now) is None
    retry_after = _record_registration_attempt(db, "person@example.com", "192.0.2.11", now)
    assert retry_after == 900
    assert db.query(RegistrationAttempt).count() == 6


def test_registration_ip_limit_is_shared_across_distinct_emails(db):
    now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
    for index in range(20):
        assert _record_registration_attempt(db, f"user{index}@example.com", "192.0.2.20", now) is None
    assert _record_registration_attempt(db, "another@example.com", "192.0.2.20", now) == 900


def test_registration_limit_expires_after_sliding_window(db):
    now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
    for _ in range(5):
        _record_registration_attempt(db, "person@example.com", "192.0.2.30", now)
    assert _record_registration_attempt(
        db, "person@example.com", "192.0.2.30", now + timedelta(minutes=16)
    ) is None


def test_registration_attempts_store_only_hashes_and_prune_old_rows(db):
    now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
    db.add(RegistrationAttempt(
        email_hash="a" * 64,
        ip_hash="b" * 64,
        attempted_at=now - timedelta(hours=25),
    ))
    db.commit()
    _record_registration_attempt(db, "private@example.com", "198.51.100.4", now)
    rows = db.query(RegistrationAttempt).all()
    assert len(rows) == 1
    assert rows[0].email_hash != "private@example.com"
    assert rows[0].ip_hash != "198.51.100.4"



def test_registration_identifiers_use_a_keyed_digest(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "first-application-secret")
    first = _registration_hash("private@example.com")
    assert len(first) == 64
    assert first != "private@example.com"

    monkeypatch.setattr(settings, "jwt_secret_key", "rotated-application-secret")
    assert _registration_hash("private@example.com") != first


def test_registration_advisory_lock_keys_are_stable_signed_bigints():
    first = _registration_lock_key("email", "a" * 64)
    assert first == _registration_lock_key("email", "a" * 64)
    assert -(2**63) <= first < 2**63
    assert first != _registration_lock_key("ip", "a" * 64)
