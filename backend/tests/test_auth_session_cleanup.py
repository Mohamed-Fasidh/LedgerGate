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


def test_admin_deactivation_revokes_sessions_and_audits(monkeypatch):
    from types import SimpleNamespace
    import app.main as main
    from app.models import Role, User
    from fastapi import HTTPException
    import pytest

    member = SimpleNamespace(
        id=21, tenant_id="tenant-a", role=Role.reviewer, is_active=True
    )
    actor = SimpleNamespace(id=7, tenant_id="tenant-a")
    captured = {"filters": None, "events": [], "revocations": []}

    class Query:
        def filter(self, *conditions):
            captured["filters"] = conditions
            return self

        def with_for_update(self):
            return self

        def first(self):
            return member

    class DB:
        def query(self, model):
            assert model is User
            return Query()

        def commit(self):
            captured["committed"] = True

    monkeypatch.setattr(
        main, "_revoke_refresh_sessions",
        lambda db, tenant_id, user_id, now: captured["revocations"].append((tenant_id, user_id)) or 4,
    )
    monkeypatch.setattr(main, "record_event", lambda *args: captured["events"].append(args))

    result = main.deactivate_tenant_user(21, db=DB(), user=actor)

    assert result == {"status": "deactivated", "user_id": 21, "revoked_sessions": 4}
    assert member.is_active is False
    assert captured["revocations"] == [("tenant-a", 21)]
    assert captured["committed"] is True
    assert captured["events"][0][4] == "tenant_user_deactivated"
    assert captured["events"][0][5]["revoked_refresh_sessions"] == 4
    rendered = [str(condition) for condition in captured["filters"]]
    assert any("users.tenant_id" in item for item in rendered)
    assert any("users.id" in item for item in rendered)

    with pytest.raises(HTTPException) as self_deactivation:
        main.deactivate_tenant_user(7, db=DB(), user=actor)
    assert self_deactivation.value.status_code == 400


def test_admin_deactivation_cannot_deactivate_another_admin(monkeypatch):
    from types import SimpleNamespace
    import app.main as main
    from app.models import Role, User
    from fastapi import HTTPException
    import pytest

    member = SimpleNamespace(id=22, tenant_id="tenant-a", role=Role.admin, is_active=True)
    actor = SimpleNamespace(id=7, tenant_id="tenant-a")

    class Query:
        def filter(self, *conditions): return self
        def with_for_update(self): return self
        def first(self): return member

    class DB:
        def query(self, model):
            assert model is User
            return Query()

    with pytest.raises(HTTPException) as blocked:
        main.deactivate_tenant_user(22, db=DB(), user=actor)
    assert blocked.value.status_code == 409
    assert member.is_active is True
