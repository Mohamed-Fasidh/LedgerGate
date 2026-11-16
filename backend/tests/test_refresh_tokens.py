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



def test_logout_locks_session_and_revokes_its_entire_refresh_family(monkeypatch):
    from datetime import datetime, timezone
    from fastapi import Response
    from starlette.requests import Request
    from app import main
    from app.models import AuthSession
    from app.security import create_refresh_token
    import uuid

    family_id = str(uuid.uuid4())
    token_id = str(uuid.uuid4())
    user = User(id=9, tenant_id="tenant-test", role=Role.admin)
    refresh_token, _ = create_refresh_token(user, token_id, family_id)
    captured = {"queries": []}

    class Query:
        def __init__(self):
            self.conditions = ()
            self.locked = False
            captured["queries"].append(self)

        def filter(self, *conditions):
            self.conditions = conditions
            return self

        def with_for_update(self):
            self.locked = True
            return self

        def first(self):
            return type("SessionRow", (), {"family_id": family_id, "revoked_at": None})()

        def update(self, values, synchronize_session=False):
            captured["update"] = values
            captured["synchronize_session"] = synchronize_session
            return 1

    class DB:
        def query(self, model):
            assert model is AuthSession
            return Query()

        def commit(self):
            captured["committed"] = True

    monkeypatch.setattr(main, "set_database_tenant", lambda db, tenant_id: None)
    request = Request({
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/api/v1/auth/logout",
        "raw_path": b"/api/v1/auth/logout", "query_string": b"",
        "headers": [
            (b"origin", b"http://localhost:3000"),
            (b"cookie", f"ledgergate_csrf=csrf-test; ledgergate_refresh={refresh_token}".encode()),
            (b"x-csrf-token", b"csrf-test"),
        ],
        "server": ("testserver", 80), "client": ("testclient", 123),
        "root_path": "",
    })
    response = Response()

    result = main.logout(request, response, None, DB())

    assert result == {"status": "logged_out"}
    assert captured["queries"][0].locked is True
    family_conditions = [str(condition) for condition in captured["queries"][1].conditions]
    assert any("auth_sessions.tenant_id" in item for item in family_conditions)
    assert any("auth_sessions.family_id" in item for item in family_conditions)
    assert any("auth_sessions.revoked_at" in item for item in family_conditions)
    assert captured["synchronize_session"] is False
    assert captured["committed"] is True


def test_refresh_locks_account_before_session_and_rejects_deactivated_user(monkeypatch):
    from datetime import datetime, timezone
    from fastapi import Response
    from starlette.requests import Request
    from app import main
    from app.models import AuthSession
    import pytest
    import uuid

    token_id = str(uuid.uuid4())
    family_id = str(uuid.uuid4())
    inactive_user = User(id=9, tenant_id="tenant-test", role=Role.admin)
    inactive_user.is_active = False
    refresh_token, _ = create_refresh_token(inactive_user, token_id, family_id)
    session_row = type("SessionRow", (), {
        "expires_at": datetime.now(timezone.utc).replace(year=datetime.now(timezone.utc).year + 1),
        "revoked_at": None,
        "family_id": family_id,
        "replaced_by_token_id": None,
    })()
    captured = {"queries": [], "committed": False}

    class Query:
        def __init__(self, model):
            self.model = model
            self.locked = False
        def filter(self, *conditions):
            return self
        def with_for_update(self):
            self.locked = True
            return self
        def first(self):
            return inactive_user if self.model is User else session_row

    class DB:
        def query(self, model):
            query = Query(model)
            captured["queries"].append(query)
            return query
        def commit(self):
            captured["committed"] = True

    monkeypatch.setattr(main, "_require_csrf", lambda request: None)
    monkeypatch.setattr(main, "set_database_tenant", lambda db, tenant_id: None)
    request = Request({
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": "/api/v1/auth/refresh",
        "raw_path": b"/api/v1/auth/refresh", "query_string": b"",
        "headers": [(b"cookie", f"ledgergate_refresh={refresh_token}".encode())],
        "server": ("testserver", 80), "client": ("testclient", 123),
        "root_path": "",
    })

    with pytest.raises(Exception) as exc:
        main.refresh_session(request, Response(), None, DB())

    assert getattr(exc.value, "status_code", None) == 401
    assert [query.model for query in captured["queries"]] == [User, AuthSession]
    assert all(query.locked for query in captured["queries"])
    assert session_row.revoked_at is not None
    assert captured["committed"] is True


def test_access_token_carries_account_version():
    from app.security import create_access_token
    from app.config import settings

    user = User(id=9, tenant_id="tenant-test", role=Role.admin)
    user.auth_token_version = 4
    claims = jwt.decode(
        create_access_token(user), settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )
    assert claims["ver"] == 4


def test_access_token_rejects_after_account_version_changes(monkeypatch):
    from fastapi import HTTPException
    from unittest.mock import Mock
    from app.security import create_access_token, get_current_user
    import pytest

    user = User(id=9, tenant_id="tenant-test", role=Role.admin)
    user.auth_token_version = 2
    token = create_access_token(user)
    user.auth_token_version = 3
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = user
    monkeypatch.setattr("app.security.set_database_tenant", lambda db, tenant: None)

    with pytest.raises(HTTPException) as error:
        get_current_user(token=token, db=db)
    assert error.value.status_code == 401


def test_legacy_access_token_without_version_defaults_to_zero(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from unittest.mock import Mock
    from app.security import get_current_user

    user = User(id=9, tenant_id="tenant-test", role=Role.admin)
    user.auth_token_version = 0
    token = jwt.encode({
        "sub": "9", "tenant": "tenant-test", "role": "admin",
        "token_type": "access", "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = user
    monkeypatch.setattr("app.security.set_database_tenant", lambda db, tenant: None)
    assert get_current_user(token=token, db=db) is user
