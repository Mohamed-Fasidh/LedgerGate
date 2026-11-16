from jose import jwt
from app.security import create_refresh_token, decode_refresh_token
from app.config import settings
from app.models import Role, User
import uuid

def test_refresh_token_has_separate_type_and_rotation_identifiers():
    user = User(id=9, tenant_id="tenant-test", role=Role.admin)
    token_id = str(uuid.uuid4())
    family_id = str(uuid.uuid4())
    token, expires = create_refresh_token(user, token_id, family_id)
    claims = decode_refresh_token(token)
    assert claims["token_type"] == "refresh"
    assert claims["jti"] == token_id
    assert claims["family"] == family_id
    assert claims["tenant"] == "tenant-test"
    assert expires is not None

def test_access_token_is_not_accepted_as_refresh_token():
    from app.security import create_access_token
    user = User(id=9, tenant_id="tenant-test", role=Role.admin)
    access = create_access_token(user)
    try:
        decode_refresh_token(access)
    except Exception:
        pass
    else:
        raise AssertionError("Access tokens must not be accepted as refresh tokens")


def test_refresh_token_is_rejected_by_access_token_validation():
    from app.security import get_current_user
    from fastapi import HTTPException
    from unittest.mock import Mock
    user = User(id=9, tenant_id="tenant-test", role=Role.admin)
    refresh, _ = create_refresh_token(user, str(uuid.uuid4()), str(uuid.uuid4()))
    with __import__("pytest").raises(HTTPException) as exc:
        get_current_user(token=refresh, db=Mock())
    assert exc.value.status_code == 401


def test_logout_all_sessions_revokes_only_current_users_active_tenant_sessions():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from app.main import logout_all_sessions
    from app.models import AuthSession

    captured = {}
    class Query:
        def __init__(self, model): self.model = model
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
            return Query(model)
        def commit(self): captured["committed"] = True
    user = SimpleNamespace(id=7, tenant_id="tenant-a")
    result = logout_all_sessions(DB(), user)
    assert result == {"status": "logged_out_everywhere", "revoked_sessions": 3}
    assert captured["committed"] is True
    assert captured["synchronize_session"] is False
    assert AuthSession.tenant_id in [condition.left for condition in captured["conditions"] if hasattr(condition, "left")]
    assert AuthSession.user_id in [condition.left for condition in captured["conditions"] if hasattr(condition, "left")]
