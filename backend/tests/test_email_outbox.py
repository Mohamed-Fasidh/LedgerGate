from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.models import OutboundEmail, EmailVerificationToken
from app.services.email_outbox import enqueue_email, process_one, _cipher


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "email_outbox_encryption_key", Fernet.generate_key().decode())
    engine = create_engine(f"sqlite:///{tmp_path / 'outbox.db'}")
    OutboundEmail.__table__.create(engine)
    EmailVerificationToken.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _enqueue(factory, url="https://app.example.com/#verify_email_token=one-time-secret"):
    with factory() as db:
        row = enqueue_email(db, "person@example.com", "email_verification", url)
        db.flush()
        row_id = row.id
        db.commit()
        return row_id


def test_outbox_encrypts_url_and_persists_message(db_factory):
    url = "https://app.example.com/#reset_token=high-entropy-secret"
    with db_factory() as db:
        row = enqueue_email(db, "person@example.com", "password_reset", url)
        db.commit()
        assert row.status == "pending"
        assert row.attempts == 0
        assert url not in row.encrypted_payload
        assert _cipher().decrypt(row.encrypted_payload.encode()).decode().find(url) >= 0


def test_worker_sends_and_marks_message_sent(db_factory):
    row_id = _enqueue(db_factory)
    delivered = []
    assert process_one(db_factory, sender=lambda row: delivered.append((row.recipient, row.message_type)))
    with db_factory() as db:
        row = db.get(OutboundEmail, row_id)
        assert (row.status, row.attempts, row.sent_at is not None) == ("sent", 1, True)
        assert row.locked_at is None
        assert row.encrypted_payload == ""
    assert delivered == [("person@example.com", "email_verification")]


def test_worker_retries_with_backoff_then_succeeds(db_factory):
    row_id = _enqueue(db_factory)
    now = datetime.now(timezone.utc)

    def fail(_row):
        raise TimeoutError("SMTP unavailable")

    assert process_one(db_factory, sender=fail, now=now)
    with db_factory() as db:
        row = db.get(OutboundEmail, row_id)
        assert row.status == "pending"
        assert row.attempts == 1
        assert row.last_error == "TimeoutError"
        assert row.next_attempt_at.replace(tzinfo=timezone.utc) == now + timedelta(seconds=30)

    assert process_one(db_factory, sender=lambda _row: pytest.fail("retry ran before due time"), now=now + timedelta(seconds=29)) is False
    assert process_one(db_factory, sender=lambda _row: None, now=now + timedelta(seconds=30))
    with db_factory() as db:
        row = db.get(OutboundEmail, row_id)
        assert row.status == "sent"
        assert row.attempts == 2
        assert row.last_error is None


def test_worker_moves_exhausted_message_to_dead_letter(db_factory):
    row_id = _enqueue(db_factory)
    with db_factory() as db:
        row = db.get(OutboundEmail, row_id)
        row.max_attempts = 1
        db.commit()

    assert process_one(db_factory, sender=lambda _row: (_ for _ in ()).throw(ConnectionError()), now=datetime.now(timezone.utc))
    with db_factory() as db:
        row = db.get(OutboundEmail, row_id)
        assert row.status == "dead"
        assert row.encrypted_payload == ""
        assert row.attempts == 1
        assert row.last_error == "ConnectionError"


def test_worker_recovers_abandoned_sending_lease(db_factory):
    row_id = _enqueue(db_factory)
    now = datetime.now(timezone.utc)
    with db_factory() as db:
        row = db.get(OutboundEmail, row_id)
        row.status = "sending"
        row.attempts = 1
        row.locked_at = now - timedelta(seconds=301)
        db.commit()

    assert process_one(db_factory, sender=lambda _row: None, now=now)
    with db_factory() as db:
        row = db.get(OutboundEmail, row_id)
        assert row.status == "sent"
        assert row.attempts == 2


def test_outbox_rejects_unknown_message_type(db_factory):
    with db_factory() as db:
        with pytest.raises(ValueError, match="Unsupported"):
            enqueue_email(db, "person@example.com", "arbitrary", "https://example.com")


def test_outbox_decrypts_payload_with_previous_rotation_key(db_factory, monkeypatch):
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "email_outbox_encryption_key", old_key)
    with db_factory() as db:
        row = enqueue_email(db, "person@example.com", "password_reset", "https://app.example.com/#reset_token=old-key")
        db.commit()
        encrypted = row.encrypted_payload
    monkeypatch.setattr(settings, "email_outbox_encryption_key", new_key)
    monkeypatch.setattr(settings, "email_outbox_decryption_keys", old_key)
    from app.services.email_outbox import _decrypt_payload
    assert _decrypt_payload(encrypted)["url"].endswith("reset_token=old-key")


def test_worker_cancels_message_when_verification_token_was_invalidated(db_factory):
    with db_factory() as db:
        row = enqueue_email(
            db, "person@example.com", "email_verification",
            "https://app.example.com/#verify_email_token=stale", "a" * 64,
        )
        db.flush()
        row_id = row.id
        db.commit()
    delivered = []
    assert process_one(db_factory, sender=lambda message: delivered.append(message))
    with db_factory() as db:
        row = db.get(OutboundEmail, row_id)
        assert row.status == "cancelled"
        assert row.encrypted_payload == ""
        assert row.locked_at is None
    assert delivered == []


def test_terminal_outbox_retention_removes_old_records(db_factory):
    from app.services.email_outbox import purge_terminal_messages

    with db_factory() as db:
        old_sent = OutboundEmail(
            recipient="old@example.com", message_type="password_reset", encrypted_payload="",
            status="sent", attempts=1, max_attempts=12,
            next_attempt_at=datetime.now(timezone.utc) - timedelta(days=40),
            created_at=datetime.now(timezone.utc) - timedelta(days=40),
        )
        recent_sent = OutboundEmail(
            recipient="recent@example.com", message_type="password_reset", encrypted_payload="",
            status="sent", attempts=1, max_attempts=12,
            next_attempt_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        old_dead = OutboundEmail(
            recipient="dead@example.com", message_type="email_verification", encrypted_payload="",
            status="dead", attempts=12, max_attempts=12,
            next_attempt_at=datetime.now(timezone.utc) - timedelta(days=100),
            created_at=datetime.now(timezone.utc) - timedelta(days=100),
        )
        db.add_all([old_sent, recent_sent, old_dead])
        db.commit()

    deleted = purge_terminal_messages(db_factory, now=datetime.now(timezone.utc))
    assert deleted == 2
    with db_factory() as db:
        rows = db.query(OutboundEmail).all()
        assert len(rows) == 1 and rows[0].recipient == "recent@example.com"


def test_worker_does_not_exceed_attempt_limit_after_crash(db_factory):
    row_id = _enqueue(db_factory)
    now = datetime.now(timezone.utc)
    with db_factory() as db:
        row = db.get(OutboundEmail, row_id)
        row.status = "sending"
        row.attempts = row.max_attempts
        row.locked_at = now - timedelta(seconds=301)
        db.commit()

    delivered = []
    assert process_one(db_factory, sender=lambda row: delivered.append(row), now=now)
    with db_factory() as db:
        row = db.get(OutboundEmail, row_id)
        assert row.status == "dead"
        assert row.last_error == "WorkerLeaseExpired"
        assert row.encrypted_payload == ""
    assert delivered == []


def test_worker_delivers_message_when_bound_token_is_still_valid(db_factory):
    now = datetime.now(timezone.utc)
    token_hash = "b" * 64
    with db_factory() as db:
        db.add(EmailVerificationToken(
            user_id=1, token_hash=token_hash,
            expires_at=now + timedelta(hours=1), created_at=now,
        ))
        row = enqueue_email(
            db, "person@example.com", "email_verification",
            "https://app.example.com/#verify_email_token=valid", token_hash,
        )
        db.flush()
        row_id = row.id
        db.commit()

    delivered = []
    assert process_one(db_factory, sender=lambda message: delivered.append(message.token_hash), now=now + timedelta(seconds=1))
    with db_factory() as db:
        row = db.get(OutboundEmail, row_id)
        assert row.status == "sent"
    assert delivered == [token_hash]
