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


def test_admin_member_directory_is_tenant_scoped_paginated_and_minimal():
    from types import SimpleNamespace
    import app.main as main
    from app.models import Role, User

    member = SimpleNamespace(
        id=31, tenant_id="tenant-a", email="member@example.test",
        role=Role.reviewer, is_active=False, mfa_enabled=True,
        created_at=datetime(2026, 11, 16, tzinfo=timezone.utc),
        hashed_password="must-never-be-returned",
        mfa_secret_encrypted="must-never-be-returned",
    )
    captured = {}

    class Query:
        def filter(self, *conditions):
            captured["conditions"] = conditions
            return self
        def order_by(self, *columns):
            captured["order"] = columns
            return self
        def offset(self, value):
            captured["offset"] = value
            return self
        def limit(self, value):
            captured["limit"] = value
            return self
        def all(self):
            return [member]

    class DB:
        def query(self, model):
            assert model is User
            return Query()

    actor = SimpleNamespace(id=7, tenant_id="tenant-a", role=Role.admin)
    result = main.list_tenant_users(limit=25, offset=50, db=DB(), user=actor)

    assert result == [{
        "id": 31,
        "email": "member@example.test",
        "role": "reviewer",
        "is_active": False,
        "mfa_enabled": True,
        "created_at": "2026-11-16T00:00:00+00:00",
    }]
    assert captured["limit"] == 25
    assert captured["offset"] == 50
    rendered = [str(condition) for condition in captured["conditions"]]
    assert any("users.tenant_id" in item for item in rendered)
    assert all("hashed_password" not in key and "mfa_secret" not in key for key in result[0])


def test_admin_member_directory_rejects_invalid_pagination():
    import app.main as main
    from fastapi import HTTPException
    import pytest

    class DB:
        def query(self, model):
            raise AssertionError("Invalid pagination must be rejected before querying")

    actor = SimpleNamespace(id=7, tenant_id="tenant-a")
    with pytest.raises(HTTPException) as too_many:
        main.list_tenant_users(limit=201, offset=0, db=DB(), user=actor)
    assert too_many.value.status_code == 400
    with pytest.raises(HTTPException) as negative_offset:
        main.list_tenant_users(limit=10, offset=-1, db=DB(), user=actor)
    assert negative_offset.value.status_code == 400


def test_admin_role_change_is_tenant_scoped_revokes_sessions_and_audits(monkeypatch):
    import app.main as main
    from app.models import Role, User
    from fastapi import HTTPException
    import pytest

    member = SimpleNamespace(
        id=42, tenant_id="tenant-a", role=Role.analyst, is_active=True,
    )
    captured = {}

    class Query:
        def filter(self, *conditions):
            captured["conditions"] = conditions
            return self
        def with_for_update(self):
            captured["locked"] = True
            return self
        def first(self):
            return member

    class DB:
        def query(self, model):
            assert model is User
            return Query()
        def commit(self):
            captured["committed"] = True

    monkeypatch.setattr(main, "_revoke_refresh_sessions", lambda db, tenant, uid, now: 3)
    monkeypatch.setattr(main, "record_event", lambda *args: captured.setdefault("audit", args))
    actor = SimpleNamespace(id=7, tenant_id="tenant-a", role=Role.admin)
    result = main.update_tenant_user_role(
        user_id=42,
        payload=main.TenantUserRoleUpdateIn(role="reviewer"),
        db=DB(),
        user=actor,
    )

    assert result == {
        "status": "updated", "user_id": 42, "role": "reviewer", "revoked_sessions": 3,
    }
    assert member.role == Role.reviewer
    assert captured["locked"] is True
    assert captured["committed"] is True
    rendered = [str(condition) for condition in captured["conditions"]]
    assert any("users.tenant_id" in item for item in rendered)
    assert captured["audit"][4] == "tenant_user_role_changed"
    assert captured["audit"][5]["previous_role"] == "analyst"
    assert captured["audit"][5]["new_role"] == "reviewer"


def test_admin_role_change_rejects_admin_and_inactive_members():
    import app.main as main
    from app.models import Role, User
    from fastapi import HTTPException
    import pytest

    class Query:
        def __init__(self, member): self.member = member
        def filter(self, *conditions): return self
        def with_for_update(self): return self
        def first(self): return self.member

    class DB:
        def __init__(self, member): self.member = member
        def query(self, model):
            assert model is User
            return Query(self.member)

    actor = SimpleNamespace(id=7, tenant_id="tenant-a", role=Role.admin)
    for member, expected in [
        (SimpleNamespace(id=50, tenant_id="tenant-a", role=Role.admin, is_active=True), 409),
        (SimpleNamespace(id=51, tenant_id="tenant-a", role=Role.analyst, is_active=False), 409),
    ]:
        with pytest.raises(HTTPException) as error:
            main.update_tenant_user_role(
                user_id=member.id,
                payload=main.TenantUserRoleUpdateIn(role="reviewer"),
                db=DB(member),
                user=actor,
            )
        assert error.value.status_code == expected


def test_user_session_listing_is_tenant_scoped_and_minimal():
    import app.main as main
    from app.models import AuthSession

    session = SimpleNamespace(
        id=8, tenant_id="tenant-a", user_id=7,
        created_at=datetime(2026, 11, 15, tzinfo=timezone.utc),
        expires_at=datetime(2026, 12, 15, tzinfo=timezone.utc),
        token_hash="never-return-this", family_id="never-return-this",
    )
    captured = {}

    class Query:
        def filter(self, *conditions):
            captured["conditions"] = conditions
            return self
        def order_by(self, *columns):
            return self
        def limit(self, value):
            captured["limit"] = value
            return self
        def all(self):
            return [session]

    class DB:
        def query(self, model):
            assert model is AuthSession
            return Query()

    actor = SimpleNamespace(id=7, tenant_id="tenant-a")
    result = main.list_auth_sessions(db=DB(), user=actor)
    assert captured["limit"] == 100
    assert result == [{
        "id": 8,
        "created_at": "2026-11-15T00:00:00+00:00",
        "expires_at": "2026-12-15T00:00:00+00:00",
    }]
    rendered = [str(condition) for condition in captured["conditions"]]
    assert any("auth_sessions.tenant_id" in item for item in rendered)
    assert any("auth_sessions.user_id" in item for item in rendered)
    assert "token_hash" not in result[0]
    assert "family_id" not in result[0]


def test_user_can_revoke_only_their_own_session_and_action_is_audited(monkeypatch):
    import app.main as main
    from app.models import AuthSession

    session = SimpleNamespace(
        id=19, tenant_id="tenant-a", user_id=7, revoked_at=None,
    )
    captured = {}

    class Query:
        def filter(self, *conditions):
            captured["conditions"] = conditions
            return self
        def with_for_update(self):
            captured["locked"] = True
            return self
        def first(self):
            return session

    class DB:
        def query(self, model):
            assert model is AuthSession
            return Query()
        def commit(self):
            captured["committed"] = True

    monkeypatch.setattr(main, "record_event", lambda *args: captured.setdefault("audit", args))
    actor = SimpleNamespace(id=7, tenant_id="tenant-a")
    result = main.revoke_auth_session(session_id=19, db=DB(), user=actor)
    assert result == {"status": "revoked", "session_id": 19}
    assert session.revoked_at is not None
    assert captured["locked"] and captured["committed"]
    rendered = [str(condition) for condition in captured["conditions"]]
    assert any("auth_sessions.tenant_id" in item for item in rendered)
    assert any("auth_sessions.user_id" in item for item in rendered)
    assert captured["audit"][4] == "auth_session_revoked"


def test_revoke_session_is_idempotent_when_already_revoked():
    import app.main as main
    from app.models import AuthSession

    session = SimpleNamespace(
        id=19, tenant_id="tenant-a", user_id=7,
        revoked_at=datetime(2026, 11, 15, tzinfo=timezone.utc),
    )

    class Query:
        def filter(self, *conditions): return self
        def with_for_update(self): return self
        def first(self): return session

    class DB:
        def query(self, model): return Query()
        def commit(self): raise AssertionError("Already-revoked session must not commit")

    result = main.revoke_auth_session(
        session_id=19, db=DB(), user=SimpleNamespace(id=7, tenant_id="tenant-a")
    )
    assert result == {"status": "already_revoked", "session_id": 19}
