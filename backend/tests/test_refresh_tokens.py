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
