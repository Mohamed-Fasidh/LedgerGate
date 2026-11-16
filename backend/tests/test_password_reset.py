from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Response, BackgroundTasks
from starlette.requests import Request

import app.main as main
from app.config import settings
from app.models import PasswordResetToken, User


def _request(origin="http://localhost:3000"):
    return Request({
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/api/v1/auth/password-reset/request",
        "raw_path": b"/api/v1/auth/password-reset/request", "query_string": b"",
        "headers": [(b"origin", origin.encode())],
        "server": ("testserver", 80), "client": ("192.0.2.5", 123),
        "root_path": "",
    })


class _Query:
    def __init__(self, model, user=None, reset=None):
        self.model, self.user, self.reset = model, user, reset
        self.conditions = ()
        self.locked = False
    def filter(self, *conditions):
        self.conditions = conditions
        return self
    def with_for_update(self):
        self.locked = True
        return self
    def first(self):
        return self.user if self.model is User else self.reset
    def update(self, values, synchronize_session=False):
        self.updated = values
        return 1


class _DB:
    def __init__(self, user=None, reset=None):
        self.user, self.reset = user, reset
        self.added, self.commits, self.rollbacks = [], 0, 0
        self.queries = []
    def query(self, model):
        query = _Query(model, self.user, self.reset)
        self.queries.append(query)
        return query
    def add(self, row):
        self.added.append(row)
    def commit(self):
        self.commits += 1
    def rollback(self):
        self.rollbacks += 1


def test_password_reset_request_response_does_not_disclose_unknown_account(monkeypatch):
    monkeypatch.setattr(main, "_record_registration_attempt", lambda *args: None)
    db = _DB(user=None)
    result = main.request_password_reset(
        main.PasswordResetRequestIn(email="nobody@example.com"), _request(), BackgroundTasks(), db
    )
    assert result["status"] == "accepted"
    assert "If the account exists" in result["message"]
    assert db.added == []


def test_password_reset_request_stores_only_hash_and_sends_expiring_link(monkeypatch):
    user = SimpleNamespace(id=7, email="person@example.com", is_active=True)
    db = _DB(user=user)
    sent = []
    monkeypatch.setattr(main, "_record_registration_attempt", lambda *args: None)
    monkeypatch.setattr(main, "send_password_reset_email", lambda email, url: sent.append((email, url)))
    monkeypatch.setattr(settings, "public_app_url", "https://app.example.com")
    tasks = BackgroundTasks()
    result = main.request_password_reset(
        main.PasswordResetRequestIn(email="person@example.com"), _request(), tasks, db
    )
    assert result["status"] == "accepted"
    assert db.commits == 1
    assert len(db.added) == 1
    stored = db.added[0]
    assert isinstance(stored, PasswordResetToken)
    assert len(stored.token_hash) == 64
    assert len(tasks.tasks) == 1
    tasks.tasks[0].func(*tasks.tasks[0].args, **tasks.tasks[0].kwargs)
    assert stored.token_hash not in sent[0][1]
    assert sent[0][0] == user.email
    assert sent[0][1].startswith("https://app.example.com/#reset_token=")
    assert stored.expires_at - stored.created_at == timedelta(minutes=30)


def test_password_reset_confirm_rejects_invalid_or_reused_tokens():
    db = _DB(reset=None)
    with pytest.raises(HTTPException) as error:
        main.confirm_password_reset(
            main.PasswordResetConfirmIn(token="x" * 48, new_password="new-password-strong"),
            _request(), Response(), db,
        )
    assert error.value.status_code == 400


def test_password_reset_confirm_changes_password_revokes_sessions_and_audits(monkeypatch):
    now = datetime.now(timezone.utc)
    reset = SimpleNamespace(
        id=1, user_id=7, used_at=None, expires_at=now + timedelta(minutes=10)
    )
    user = SimpleNamespace(
        id=7, tenant_id="tenant-7", is_active=True, email="person@example.com",
        hashed_password="old-hash", mfa_enabled=False, auth_token_version=2,
        mfa_secret_encrypted=None, mfa_recovery_code_hashes=[], mfa_last_used_step=None,
    )
    db = _DB(user=user, reset=reset)
    events, revoked = [], []
    monkeypatch.setattr(main, "verify_password", lambda plain, hashed: False)
    monkeypatch.setattr(main, "hash_password", lambda password: "new-hash")
    monkeypatch.setattr(main, "_revoke_refresh_sessions", lambda *args: revoked.append(args) or 3)
    monkeypatch.setattr(main, "set_database_tenant", lambda *args: None)
    monkeypatch.setattr(main, "record_event", lambda *args: events.append(args))
    payload = main.PasswordResetConfirmIn(token="x" * 48, new_password="new-password-strong")
    response = Response()
    result = main.confirm_password_reset(payload, _request(), response, db)
    assert result == {"status": "password_reset", "revoked_sessions": 3}
    assert user.hashed_password == "new-hash"
    assert user.auth_token_version == 3
    assert reset.used_at is not None
    assert db.commits == 1
    assert revoked and events
    cookies = response.headers.getlist("set-cookie")
    assert any(value.startswith("ledgergate_refresh=") and "max-age=0" in value.lower() for value in cookies)
    assert any(value.startswith("ledgergate_csrf=") and "max-age=0" in value.lower() for value in cookies)
    assert events[0][4] == "password_reset_completed"


def test_password_reset_request_rejects_untrusted_origin(monkeypatch):
    monkeypatch.setattr(main, "_record_registration_attempt", lambda *args: None)
    with pytest.raises(HTTPException) as error:
        main.request_password_reset(
            main.PasswordResetRequestIn(email="person@example.com"),
            _request(origin="https://attacker.example"), BackgroundTasks(), _DB(),
        )
    assert error.value.status_code == 403
