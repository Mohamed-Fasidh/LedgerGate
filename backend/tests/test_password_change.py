from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

import app.main as main


def _account(*, mfa_enabled=False):
    return SimpleNamespace(
        id=12,
        tenant_id="tenant-a",
        is_active=True,
        hashed_password="old-hash",
        mfa_enabled=mfa_enabled,
        mfa_secret_encrypted="encrypted-secret" if mfa_enabled else None,
        mfa_last_used_step=100 if mfa_enabled else None,
        mfa_recovery_code_hashes=["unused-recovery-hash"] if mfa_enabled else [],
    )


class _Query:
    def __init__(self, account):
        self.account = account
        self.locked = False
    def filter(self, *conditions):
        self.conditions = conditions
        return self
    def with_for_update(self):
        self.locked = True
        return self
    def first(self):
        return self.account


class _DB:
    def __init__(self, account):
        self.account = account
        self.query_obj = _Query(account)
        self.committed = False
    def query(self, model):
        return self.query_obj
    def commit(self):
        self.committed = True


def _payload(**overrides):
    values = {
        "current_password": "correct-current-password",
        "new_password": "a-different-strong-password",
        "totp_code": None,
        "recovery_code": None,
    }
    values.update(overrides)
    return main.ChangePasswordIn(**values)


def test_password_change_requires_correct_current_password(monkeypatch):
    account = _account()
    db = _DB(account)
    monkeypatch.setattr(main, "verify_password", lambda plain, hashed: plain == "a-different-strong-password")
    with pytest.raises(HTTPException) as error:
        main.change_password(_payload(), Response(), db, account)
    assert error.value.status_code == 403
    assert account.hashed_password == "old-hash"
    assert not db.committed


def test_password_change_rejects_reusing_current_password(monkeypatch):
    account = _account()
    db = _DB(account)
    monkeypatch.setattr(main, "verify_password", lambda plain, hashed: plain in {
        "correct-current-password", "a-different-strong-password"
    })
    with pytest.raises(HTTPException) as error:
        main.change_password(_payload(), Response(), db, account)
    assert error.value.status_code == 400
    assert not db.committed


def test_password_change_requires_mfa_when_enabled(monkeypatch):
    account = _account(mfa_enabled=True)
    db = _DB(account)
    monkeypatch.setattr(main, "verify_password", lambda plain, hashed: plain == "correct-current-password")
    monkeypatch.setattr(main, "decrypt_totp_secret", lambda encrypted: "totp-secret")
    monkeypatch.setattr(main, "matching_totp_step", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "consume_mfa_recovery_code", lambda hashes, code: None)
    with pytest.raises(HTTPException) as error:
        main.change_password(_payload(), Response(), db, account)
    assert error.value.status_code == 400
    assert account.hashed_password == "old-hash"
    assert not db.committed


def test_password_change_updates_hash_revokes_sessions_audits_and_clears_cookies(monkeypatch):
    account = _account(mfa_enabled=True)
    db = _DB(account)
    events = []
    revoked = []
    monkeypatch.setattr(main, "verify_password", lambda plain, hashed: plain == "correct-current-password")
    monkeypatch.setattr(main, "decrypt_totp_secret", lambda encrypted: "totp-secret")
    monkeypatch.setattr(main, "matching_totp_step", lambda *args, **kwargs: 12345)
    monkeypatch.setattr(main, "hash_password", lambda password: "new-hash")
    monkeypatch.setattr(
        main, "_revoke_refresh_sessions",
        lambda db, tenant, user_id, now: revoked.append((tenant, user_id, now)) or 3,
    )
    monkeypatch.setattr(main, "record_event", lambda *args: events.append(args))

    response = Response()
    result = main.change_password(_payload(totp_code="123456"), response, db, account)

    assert result == {"status": "password_changed", "revoked_sessions": 3}
    assert db.query_obj.locked and db.committed
    assert account.hashed_password == "new-hash"
    assert account.mfa_last_used_step == 12345
    assert revoked and revoked[0][:2] == ("tenant-a", 12)
    assert events and events[0][4] == "password_changed"
    assert events[0][5] == {"revoked_refresh_sessions": 3, "mfa_verified": True}
    cookies = response.headers.getlist("set-cookie")
    assert any(value.startswith("ledgergate_refresh=") and "max-age=0" in value.lower() for value in cookies)
    assert any(value.startswith("ledgergate_csrf=") and "max-age=0" in value.lower() for value in cookies)


def test_password_change_can_consume_one_recovery_code(monkeypatch):
    account = _account(mfa_enabled=True)
    db = _DB(account)
    monkeypatch.setattr(main, "verify_password", lambda plain, hashed: plain == "correct-current-password")
    monkeypatch.setattr(main, "decrypt_totp_secret", lambda encrypted: "totp-secret")
    monkeypatch.setattr(main, "matching_totp_step", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "consume_mfa_recovery_code", lambda hashes, code: ["remaining-hash"])
    monkeypatch.setattr(main, "hash_password", lambda password: "new-hash")
    monkeypatch.setattr(main, "_revoke_refresh_sessions", lambda *args: 1)
    monkeypatch.setattr(main, "record_event", lambda *args: None)

    result = main.change_password(
        _payload(recovery_code="A" * 24), Response(), db, account
    )
    assert result["status"] == "password_changed"
    assert account.mfa_recovery_code_hashes == ["remaining-hash"]
