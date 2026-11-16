from datetime import datetime, timezone
from types import SimpleNamespace

from app.models import AuthSession
from app.main import _delete_expired_auth_sessions


def test_expired_session_cleanup_is_tenant_scoped_and_bounded():
    expired = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    captured = {}

    class Query:
        def filter(self, *conditions):
            captured["conditions"] = conditions
            return self

        def order_by(self, *columns):
            captured["order_by"] = columns
            return self

        def limit(self, amount):
            captured["limit"] = amount
            return self

        def all(self):
            return expired

    class DB:
        def query(self, model):
            assert model is AuthSession
            return Query()

        def delete(self, session):
            captured.setdefault("deleted", []).append(session)

    cutoff = datetime(2026, 11, 1, tzinfo=timezone.utc)
    deleted = _delete_expired_auth_sessions(DB(), "tenant-a", cutoff, batch_size=20)

    assert deleted == 2
    assert captured["limit"] == 20
    assert captured["deleted"] == expired
    rendered = [str(condition) for condition in captured["conditions"]]
    assert any("auth_sessions.tenant_id" in item for item in rendered)
    assert any("auth_sessions.expires_at" in item for item in rendered)


def test_expired_session_cleanup_handles_empty_batch():
    class Query:
        def filter(self, *conditions): return self
        def order_by(self, *columns): return self
        def limit(self, amount): return self
        def all(self): return []

    class DB:
        def query(self, model): return Query()
        def delete(self, session): raise AssertionError("No sessions should be deleted")

    assert _delete_expired_auth_sessions(
        DB(), "tenant-empty", datetime.now(timezone.utc), batch_size=10
    ) == 0
