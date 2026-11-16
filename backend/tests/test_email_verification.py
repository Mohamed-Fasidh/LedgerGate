from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from starlette.requests import Request

import app.main as main
from app.config import settings
from app.models import EmailVerificationToken, User


def _request(origin="http://localhost:3000"):
    return Request({
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/api/v1/auth/email-verification/request",
        "raw_path": b"/api/v1/auth/email-verification/request", "query_string": b"",
        "headers": [(b"origin", origin.encode())],
        "server": ("testserver", 80), "client": ("192.0.2.8", 123),
        "root_path": "",
    })


class _Query:
    def __init__(self, model, user=None, verification=None):
        self.model, self.user, self.verification = model, user, verification
        self.conditions = ()
        self.locked = False
    def filter(self, *conditions):
        self.conditions = conditions
        return self
    def with_for_update(self):
        self.locked = True
        return self
    def first(self):
        return self.user if self.model is User else self.verification
    def update(self, values, synchronize_session=False):
        self.updated = values
        return 1
    def delete(self, synchronize_session=False):
        self.deleted = True
        return 1


class _DB:
    def __init__(self, user=None, verification=None):
        self.user, self.verification = user, verification
        self.added, self.commits, self.rollbacks, self.queries = [], 0, 0, []
    def query(self, model):
        query = _Query(model, self.user, self.verification)
        self.queries.append(query)
        return query
    def add(self, row):
        self.added.append(row)
    def flush(self):
        for row in self.added:
            if isinstance(row, User) and not getattr(row, "id", None):
                row.id = 7
    def commit(self):
        self.commits += 1
    def rollback(self):
        self.rollbacks += 1
    def refresh(self, row):
        if not getattr(row, "id", None):
            row.id = 7


def test_registration_creates_unverified_account_and_emails_single_use_link(monkeypatch):
    db = _DB()
    queued = []
    monkeypatch.setattr(main, "enqueue_email", lambda db, email, kind, url, token_hash: queued.append((email, kind, url, token_hash)))
    monkeypatch.setattr(main, "_record_registration_attempt", lambda *args: None)
    monkeypatch.setattr(main, "hash_password", lambda value: "hashed-password")
    monkeypatch.setattr(settings, "public_app_url", "https://app.example.com")

    result = main.register(
        main.RegisterIn(email="person@example.com", password="long-strong-password", tenant_name="Example Workspace"),
        _request(), db,
    )

    user, token = db.added
    assert result["status"] == "accepted"
    assert "verification link" in result["message"]
    assert user.email_verified is False
    assert isinstance(token, EmailVerificationToken)
    assert len(token.token_hash) == 64
    assert token.expires_at - token.created_at == timedelta(hours=24)
    assert queued[0][0:2] == ("person@example.com", "email_verification")
    assert queued[0][2].startswith("https://app.example.com/#verify_email_token=")
    assert queued[0][3] == token.token_hash
    assert token.token_hash not in queued[0][2]


def test_verification_resend_is_generic_and_queues_new_link(monkeypatch):
    user = SimpleNamespace(id=7, email="person@example.com", is_active=True, email_verified=False)
    db = _DB(user=user)
    queued = []
    monkeypatch.setattr(main, "enqueue_email", lambda db, email, kind, url, token_hash: queued.append((email, kind, url, token_hash)))
    monkeypatch.setattr(main, "_record_registration_attempt", lambda *args: None)
    monkeypatch.setattr(settings, "public_app_url", "https://app.example.com")

    result = main.request_email_verification(
        main.EmailVerificationRequestIn(email="person@example.com"), _request(), db
    )

    assert result["status"] == "accepted"
    assert "If this address is eligible" in result["message"]
    assert db.queries[0].locked  # user lock serializes verification-link replacement
    assert len(db.added) == 1 and isinstance(db.added[0], EmailVerificationToken)
    assert queued[0][0:2] == ("person@example.com", "email_verification")
    assert queued[0][2].startswith("https://app.example.com/#verify_email_token=")


def test_verification_request_does_not_disclose_unknown_email(monkeypatch):
    monkeypatch.setattr(main, "_record_registration_attempt", lambda *args: None)
    result = main.request_email_verification(
        main.EmailVerificationRequestIn(email="unknown@example.com"),
        _request(), _DB(user=None),
    )
    assert result["status"] == "accepted"
    assert "If this address is eligible" in result["message"]


def test_email_verification_confirm_activates_account_and_audits(monkeypatch):
    now = datetime.now(timezone.utc)
    verification = SimpleNamespace(
        id=1, user_id=7, used_at=None, expires_at=now + timedelta(hours=2)
    )
    user = SimpleNamespace(
        id=7, tenant_id="tenant-7", email="person@example.com",
        is_active=True, email_verified=False,
    )
    db = _DB(user=user, verification=verification)
    events = []
    monkeypatch.setattr(main, "set_database_tenant", lambda *args: None)
    monkeypatch.setattr(main, "record_event", lambda *args: events.append(args))

    result = main.confirm_email_verification(
        main.EmailVerificationConfirmIn(token="v" * 48), _request(), db
    )

    assert result["status"] == "email_verified"
    assert db.queries[1].locked  # lock order is user, then verification token
    assert db.queries[2].locked
    assert user.email_verified is True
    assert verification.used_at is not None
    assert db.commits == 1
    assert events and events[0][4] == "email_verified"


def test_email_verification_rejects_invalid_or_reused_tokens():
    db = _DB(verification=None)
    with pytest.raises(HTTPException) as error:
        main.confirm_email_verification(
            main.EmailVerificationConfirmIn(token="v" * 48), _request(), db
        )
    assert error.value.status_code == 400


def test_login_eligibility_requires_verified_active_account():
    assert main._login_credentials_are_valid(
        SimpleNamespace(is_active=True, email_verified=True), True
    )
    assert not main._login_credentials_are_valid(
        SimpleNamespace(is_active=True, email_verified=False), True
    )
    assert not main._login_credentials_are_valid(
        SimpleNamespace(is_active=False, email_verified=True), True
    )


def test_duplicate_registration_is_generic_and_resends_only_unverified_account(monkeypatch):
    existing = SimpleNamespace(
        id=7, email="person@example.com", is_active=True, email_verified=False
    )

    class DuplicateDB(_DB):
        def flush(self):
            raise IntegrityError("INSERT", {}, RuntimeError("duplicate email"))
        def rollback(self):
            super().rollback()
            self.added.clear()

    db = DuplicateDB(user=existing)
    queued = []
    monkeypatch.setattr(main, "enqueue_email", lambda db, email, kind, url, token_hash: queued.append((email, kind, url, token_hash)))
    monkeypatch.setattr(main, "_record_registration_attempt", lambda *args: None)
    monkeypatch.setattr(main, "hash_password", lambda value: "hashed-password")
    monkeypatch.setattr(settings, "public_app_url", "https://app.example.com")

    result = main.register(
        main.RegisterIn(email="person@example.com", password="long-strong-password", tenant_name="Other Workspace"),
        _request(), db,
    )

    assert result == {"status": "accepted", "message": main.EMAIL_VERIFICATION_GENERIC_MESSAGE}
    assert db.rollbacks == 1
    assert len(db.added) == 1 and isinstance(db.added[0], EmailVerificationToken)
    assert queued[0][0:2] == ("person@example.com", "email_verification")


def test_duplicate_verified_registration_does_not_disclose_or_send_email(monkeypatch):
    existing = SimpleNamespace(
        id=7, email="person@example.com", is_active=True, email_verified=True
    )

    class DuplicateDB(_DB):
        def flush(self):
            raise IntegrityError("INSERT", {}, RuntimeError("duplicate email"))
        def rollback(self):
            super().rollback()
            self.added.clear()

    db = DuplicateDB(user=existing)
    queued = []
    monkeypatch.setattr(main, "enqueue_email", lambda db, email, kind, url, token_hash: queued.append((email, kind, url, token_hash)))
    monkeypatch.setattr(main, "_record_registration_attempt", lambda *args: None)
    monkeypatch.setattr(main, "hash_password", lambda value: "hashed-password")

    result = main.register(
        main.RegisterIn(email="person@example.com", password="long-strong-password", tenant_name="Other Workspace"),
        _request(), db,
    )

    assert result == {"status": "accepted", "message": main.EMAIL_VERIFICATION_GENERIC_MESSAGE}
    assert not queued
    assert not db.added
