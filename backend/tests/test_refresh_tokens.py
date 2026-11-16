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


def test_revoke_refresh_sessions_is_scoped_to_tenant_and_user():
    from datetime import datetime, timezone
    from app.main import _revoke_refresh_sessions
    from app.models import AuthSession

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
    now = datetime.now(timezone.utc)
    assert _revoke_refresh_sessions(DB(), "tenant-a", 7, now) == 3
    rendered = [str(condition) for condition in captured["conditions"]]
    assert any("auth_sessions.tenant_id" in item for item in rendered)
    assert any("auth_sessions.user_id" in item for item in rendered)
    assert any("auth_sessions.revoked_at" in item for item in rendered)
    assert any("auth_sessions.expires_at" in item for item in rendered)
    assert captured["values"][AuthSession.revoked_at] == now
    assert captured["synchronize_session"] is False


def _csrf_request(origin="http://localhost:3000", cookie="csrf-test", header="csrf-test"):
    from starlette.requests import Request
    headers = [
        (b"origin", origin.encode()),
        (b"cookie", f"ledgergate_csrf={cookie}".encode()),
        (b"x-csrf-token", header.encode()),
    ]
    scope = {
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/api/v1/auth/refresh", "raw_path": b"/api/v1/auth/refresh",
        "query_string": b"", "headers": headers, "server": ("testserver", 80),
        "client": ("testclient", 123), "root_path": "",
    }
    return Request(scope)


def test_refresh_cookie_is_httponly_and_csrf_cookie_is_separate(monkeypatch):
    from fastapi import Response
    from app.config import settings
    from app.main import _set_auth_cookies

    monkeypatch.setattr(settings, "app_env", "production")
    response = Response()
    _set_auth_cookies(response, "secret-refresh", csrf_token="csrf-value")
    cookies = response.headers.getlist("set-cookie")
    refresh_cookie = next(value for value in cookies if value.startswith("ledgergate_refresh="))
    csrf_cookie = next(value for value in cookies if value.startswith("ledgergate_csrf="))
    assert "httponly" in refresh_cookie.lower()
    assert "secure" in refresh_cookie.lower()
    assert "samesite=lax" in refresh_cookie.lower()
    assert "path=/api/v1/auth" in refresh_cookie.lower()
    assert "httponly" not in csrf_cookie.lower()
    assert "secure" in csrf_cookie.lower()


def test_refresh_and_logout_csrf_validation_requires_allowed_origin_and_matching_token():
    from fastapi import HTTPException
    from app.main import _require_csrf
    import pytest

    _require_csrf(_csrf_request())
    with pytest.raises(HTTPException) as mismatch:
        _require_csrf(_csrf_request(header="wrong"))
    assert mismatch.value.status_code == 403
    with pytest.raises(HTTPException) as bad_origin:
        _require_csrf(_csrf_request(origin="https://attacker.example"))
    assert bad_origin.value.status_code == 403


def test_logout_clears_refresh_and_csrf_cookies():
    from fastapi import Response
    from app.main import _clear_auth_cookies

    response = Response()
    _clear_auth_cookies(response)
    cookies = response.headers.getlist("set-cookie")
    assert any(value.startswith("ledgergate_refresh=") and "max-age=0" in value.lower() for value in cookies)
    assert any(value.startswith("ledgergate_csrf=") and "max-age=0" in value.lower() for value in cookies)


def test_login_and_registration_reject_untrusted_browser_origins():
    from fastapi import HTTPException
    from app.main import _require_allowed_origin
    import pytest

    _require_allowed_origin(_csrf_request(origin="http://localhost:3000"))
    with pytest.raises(HTTPException) as blocked:
        _require_allowed_origin(_csrf_request(origin="https://attacker.example"))
    assert blocked.value.status_code == 403
