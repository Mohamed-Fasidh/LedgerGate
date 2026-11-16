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
