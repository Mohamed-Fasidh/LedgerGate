from datetime import datetime, timezone

from app.main import _revoke_refresh_sessions
from app.models import AuthSession


def test_mfa_disable_revokes_only_active_sessions_for_the_user_and_tenant():
    captured = {}

    class Query:
        def filter(self, *conditions):
            captured["conditions"] = conditions
            return self

        def update(self, values, synchronize_session=False):
            captured["values"] = values
            captured["synchronize_session"] = synchronize_session
            return 3

    class DB:
        def query(self, model):
            assert model is AuthSession
            return Query()

    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    assert _revoke_refresh_sessions(DB(), "tenant-a", 42, now) == 3

    rendered = [str(condition) for condition in captured["conditions"]]
    assert any("auth_sessions.tenant_id" in item for item in rendered)
    assert any("auth_sessions.user_id" in item for item in rendered)
    assert any("auth_sessions.revoked_at" in item for item in rendered)
    assert any("auth_sessions.expires_at" in item for item in rendered)
    assert captured["synchronize_session"] is False
    assert list(captured["values"].keys()) == [AuthSession.revoked_at]
    assert captured["values"][AuthSession.revoked_at] == now


def test_disabling_mfa_revokes_sessions_and_clears_auth_cookies(monkeypatch):
    from types import SimpleNamespace
    from fastapi import Response
    import app.main as main

    locked_user = SimpleNamespace(
        id=42,
        tenant_id="tenant-a",
        mfa_enabled=True,
        mfa_secret_encrypted="encrypted-secret",
        mfa_enabled_at=datetime.now(timezone.utc),
        mfa_last_used_step=10,
        mfa_recovery_code_hashes=["unused-hash"],
        hashed_password="password-hash",
    )
    user = SimpleNamespace(id=42, tenant_id="tenant-a")
    events = []
    revoked = []

    class Query:
        def filter(self, *conditions):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return locked_user

    class DB:
        def query(self, model):
            return Query()

        def commit(self):
            pass

    monkeypatch.setattr(
        main, "_begin_sensitive_auth_attempt",
        lambda db, user, request: ("email-hash", "ip-hash", datetime.now(timezone.utc)),
    )
    monkeypatch.setattr(main, "_record_sensitive_auth_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "verify_password", lambda password, hashed: True)
    monkeypatch.setattr(main, "decrypt_totp_secret", lambda encrypted: "totp-secret")
    monkeypatch.setattr(main, "matching_totp_step", lambda *args, **kwargs: 11)
    monkeypatch.setattr(
        main, "_revoke_refresh_sessions",
        lambda db, tenant_id, user_id, now: revoked.append((tenant_id, user_id)) or 2,
    )
    monkeypatch.setattr(main, "record_event", lambda *args, **kwargs: events.append(args))
    response = Response()

    result = main.disable_mfa(
        request=SimpleNamespace(),
        response=response,
        current_password="current-password",
        totp_code="123456",
        recovery_code=None,
        db=DB(),
        user=user,
    )

    assert result == {"status": "disabled", "revoked_sessions": 2}
    assert revoked == [("tenant-a", 42)]
    assert locked_user.mfa_enabled is False
    assert locked_user.mfa_secret_encrypted is None
    assert locked_user.mfa_recovery_code_hashes == []
    assert events and events[0][5]["revoked_refresh_sessions"] == 2
    cookies = response.headers.getlist("set-cookie")
    assert any(value.startswith("ledgergate_refresh=") and "max-age=0" in value.lower() for value in cookies)
    assert any(value.startswith("ledgergate_csrf=") and "max-age=0" in value.lower() for value in cookies)
