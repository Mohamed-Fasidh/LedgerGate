from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture
def auth_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def register_account(client, email="api-auth@example.com"):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "LedgerGate-api-test-2026",
            "tenant_name": "API Test Workspace",
        },
    )
    return response


def test_register_returns_access_and_refresh_tokens(auth_client):
    response = register_account(auth_client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["access_token"]
    assert payload["refresh_token"]
    assert payload["token_type"] == "bearer"

    me = auth_client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {payload['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == "api-auth@example.com"


def test_registration_rejects_duplicate_email(auth_client):
    first = register_account(auth_client)
    duplicate = register_account(auth_client)

    assert first.status_code == 200
    assert duplicate.status_code == 409


def test_login_and_refresh_rotation_rejects_replay(auth_client):
    registered = register_account(auth_client)
    assert registered.status_code == 200

    login = auth_client.post(
        "/api/v1/auth/token",
        data={
            "username": " API-AUTH@example.com ",
            "password": "LedgerGate-api-test-2026",
        },
    )
    assert login.status_code == 200
    original = login.json()
    assert original["access_token"]
    assert original["refresh_token"]

    rotated = auth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": original["refresh_token"]},
    )
    assert rotated.status_code == 200
    replacement = rotated.json()
    assert replacement["access_token"] != original["access_token"]
    assert replacement["refresh_token"] != original["refresh_token"]

    replay = auth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": original["refresh_token"]},
    )
    assert replay.status_code == 401


def test_logout_revokes_access_and_paired_refresh_token(auth_client):
    registered = register_account(auth_client)
    assert registered.status_code == 200
    tokens = registered.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    logout = auth_client.post("/api/v1/auth/logout", headers=headers)
    assert logout.status_code == 200
    assert logout.json() == {"revoked": True}

    protected = auth_client.get("/api/v1/me", headers=headers)
    assert protected.status_code == 401

    refresh = auth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert refresh.status_code == 401


def test_logout_requires_authentication(auth_client):
    response = auth_client.post("/api/v1/auth/logout")
    assert response.status_code == 401


def test_password_reset_is_one_time_and_revokes_existing_sessions(auth_client, monkeypatch):
    from app import main

    registered = register_account(auth_client, "recovery@example.com")
    assert registered.status_code == 200
    delivered = []
    monkeypatch.setattr(main, "send_password_reset_email", lambda email, token: delivered.append((email, token)))

    requested = auth_client.post("/api/v1/auth/password-reset/request", json={"email": "recovery@example.com"})
    unknown = auth_client.post("/api/v1/auth/password-reset/request", json={"email": "nobody@example.com"})
    assert requested.status_code == unknown.status_code == 202
    assert requested.json() == unknown.json()
    assert len(delivered) == 1

    reset = auth_client.post("/api/v1/auth/password-reset/confirm", json={
        "token": delivered[0][1], "password": "New-ledgergate-password-2026",
    })
    assert reset.status_code == 200
    assert auth_client.get("/api/v1/me", headers={"Authorization": f"Bearer {registered.json()['access_token']}"}).status_code == 401
    assert auth_client.post("/api/v1/auth/password-reset/confirm", json={
        "token": delivered[0][1], "password": "Another-ledgergate-password-2026",
    }).status_code == 400
    login = auth_client.post("/api/v1/auth/token", data={
        "username": "recovery@example.com", "password": "New-ledgergate-password-2026",
    })
    assert login.status_code == 200


def test_password_reset_establishes_tenant_context_before_session_revocation(auth_client, monkeypatch):
    from app import main, security

    registered = register_account(auth_client, "rls-recovery@example.com")
    assert registered.status_code == 200
    delivered = []
    monkeypatch.setattr(
        main,
        "send_password_reset_email",
        lambda email, token: delivered.append(token),
    )
    requested = auth_client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": "rls-recovery@example.com"},
    )
    assert requested.status_code == 202
    assert len(delivered) == 1

    tenant_contexts = []
    original_set_tenant_context = security.set_tenant_context

    def track_tenant_context(db, tenant_id):
        tenant_contexts.append(tenant_id)
        return original_set_tenant_context(db, tenant_id)

    monkeypatch.setattr(security, "set_tenant_context", track_tenant_context)
    confirmed = auth_client.post(
        "/api/v1/auth/password-reset/confirm",
        json={
            "token": delivered[0],
            "password": "New-rls-recovery-password-2026",
        },
    )

    assert confirmed.status_code == 200
    assert len(tenant_contexts) == 1
    assert tenant_contexts[0]


def test_mfa_enrollment_login_and_disable_flow(auth_client, monkeypatch):
    from app import security

    monkeypatch.setattr(
        security.settings,
        "app_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    registered = register_account(auth_client, "mfa-api@example.com")
    assert registered.status_code == 200
    initial = registered.json()
    auth_headers = {"Authorization": f"Bearer {initial['access_token']}"}

    enrollment = auth_client.post(
        "/api/v1/auth/mfa/enroll",
        headers=auth_headers,
    )
    assert enrollment.status_code == 200
    secret = enrollment.json()["secret"]
    assert enrollment.json()["provisioning_uri"].startswith("otpauth://totp/")
    assert secret

    code = security.totp_code(secret)
    verified = auth_client.post(
        "/api/v1/auth/mfa/verify-enrollment",
        headers=auth_headers,
        data={"code": code},
    )
    assert verified.status_code == 200
    assert verified.json()["mfa_enabled"] is True

    missing_code = auth_client.post(
        "/api/v1/auth/token",
        data={
            "username": "mfa-api@example.com",
            "password": "LedgerGate-api-test-2026",
        },
    )
    assert missing_code.status_code == 401

    mfa_login = auth_client.post(
        "/api/v1/auth/token",
        data={
            "username": "mfa-api@example.com",
            "password": "LedgerGate-api-test-2026",
            "totp_code": security.totp_code(secret),
        },
    )
    assert mfa_login.status_code == 200
    logged_in = mfa_login.json()
    login_headers = {"Authorization": f"Bearer {logged_in['access_token']}"}

    disabled = auth_client.post(
        "/api/v1/auth/mfa/disable",
        headers=login_headers,
        data={
            "password": "LedgerGate-api-test-2026",
            "code": security.totp_code(secret),
        },
    )
    assert disabled.status_code == 200
    assert disabled.json()["mfa_enabled"] is False

    password_login = auth_client.post(
        "/api/v1/auth/token",
        data={
            "username": "mfa-api@example.com",
            "password": "LedgerGate-api-test-2026",
        },
    )
    assert password_login.status_code == 200


def test_mfa_enrollment_rejects_invalid_code(auth_client, monkeypatch):
    from app import security

    monkeypatch.setattr(
        security.settings,
        "app_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    registered = register_account(auth_client, "mfa-invalid@example.com")
    assert registered.status_code == 200
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}

    enrollment = auth_client.post("/api/v1/auth/mfa/enroll", headers=headers)
    assert enrollment.status_code == 200

    invalid = auth_client.post(
        "/api/v1/auth/mfa/verify-enrollment",
        headers=headers,
        data={"code": "000000"},
    )
    assert invalid.status_code == 400


def test_mfa_enrollment_verification_is_throttled_after_repeated_invalid_codes(auth_client, monkeypatch):
    from app import security

    monkeypatch.setattr(
        security.settings,
        "app_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    registered = register_account(auth_client, "mfa-throttle@example.com")
    assert registered.status_code == 200
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    enrollment = auth_client.post("/api/v1/auth/mfa/enroll", headers=headers)
    assert enrollment.status_code == 200

    for _ in range(5):
        response = auth_client.post(
            "/api/v1/auth/mfa/verify-enrollment",
            headers=headers,
            data={"code": "000000"},
        )
        assert response.status_code == 400

    locked = auth_client.post(
        "/api/v1/auth/mfa/verify-enrollment",
        headers=headers,
        data={"code": "000000"},
    )
    assert locked.status_code == 429


def test_mfa_disable_invalid_password_is_throttled(auth_client, monkeypatch):
    from app import security

    monkeypatch.setattr(
        security.settings,
        "app_encryption_key",
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    registered = register_account(auth_client, "mfa-disable-throttle@example.com")
    assert registered.status_code == 200
    initial = registered.json()
    headers = {"Authorization": f"Bearer {initial['access_token']}"}

    enrollment = auth_client.post("/api/v1/auth/mfa/enroll", headers=headers)
    assert enrollment.status_code == 200
    secret = enrollment.json()["secret"]
    enabled = auth_client.post(
        "/api/v1/auth/mfa/verify-enrollment",
        headers=headers,
        data={"code": security.totp_code(secret)},
    )
    assert enabled.status_code == 200

    for _ in range(5):
        response = auth_client.post(
            "/api/v1/auth/mfa/disable",
            headers=headers,
            data={"password": "incorrect-password", "code": security.totp_code(secret)},
        )
        assert response.status_code == 401

    locked = auth_client.post(
        "/api/v1/auth/mfa/disable",
        headers=headers,
        data={
            "password": "LedgerGate-api-test-2026",
            "code": security.totp_code(secret),
        },
    )
    assert locked.status_code == 429


def test_user_can_list_sessions_and_revoke_another_session(auth_client):
    registered = register_account(auth_client, "sessions-api@example.com")
    assert registered.status_code == 200
    first_tokens = registered.json()
    first_headers = {"Authorization": f"Bearer {first_tokens['access_token']}"}

    login = auth_client.post(
        "/api/v1/auth/token",
        data={
            "username": "sessions-api@example.com",
            "password": "LedgerGate-api-test-2026",
        },
    )
    assert login.status_code == 200
    second_tokens = login.json()

    listing = auth_client.get("/api/v1/auth/sessions", headers=first_headers)
    assert listing.status_code == 200
    sessions = listing.json()
    assert len(sessions) == 2
    current = [item for item in sessions if item["current"]]
    other = [item for item in sessions if not item["current"]]
    assert len(current) == 1
    assert len(other) == 1

    revoked = auth_client.delete(
        f"/api/v1/auth/sessions/{other[0]['id']}",
        headers=first_headers,
    )
    assert revoked.status_code == 200
    assert revoked.json() == {"revoked": True}

    second_headers = {"Authorization": f"Bearer {second_tokens['access_token']}"}
    denied = auth_client.get("/api/v1/me", headers=second_headers)
    assert denied.status_code == 401

    refreshed = auth_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": second_tokens["refresh_token"]},
    )
    assert refreshed.status_code == 401


def test_session_revocation_cannot_target_another_users_session(auth_client):
    first = register_account(auth_client, "session-owner@example.com")
    second = register_account(auth_client, "session-other@example.com")
    assert first.status_code == 200
    assert second.status_code == 200

    first_headers = {"Authorization": f"Bearer {first.json()['access_token']}"}
    second_headers = {"Authorization": f"Bearer {second.json()['access_token']}"}
    other_sessions = auth_client.get("/api/v1/auth/sessions", headers=second_headers)
    assert other_sessions.status_code == 200
    other_id = other_sessions.json()[0]["id"]

    response = auth_client.delete(
        f"/api/v1/auth/sessions/{other_id}",
        headers=first_headers,
    )
    assert response.status_code == 404

    still_valid = auth_client.get("/api/v1/me", headers=second_headers)
    assert still_valid.status_code == 200


def test_session_delete_rejects_current_session_and_requires_logout(auth_client):
    registered = register_account(auth_client, "session-current@example.com")
    assert registered.status_code == 200
    tokens = registered.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    listing = auth_client.get("/api/v1/auth/sessions", headers=headers)
    assert listing.status_code == 200
    current_id = listing.json()[0]["id"]

    response = auth_client.delete(
        f"/api/v1/auth/sessions/{current_id}",
        headers=headers,
    )
    assert response.status_code == 409
    still_valid = auth_client.get("/api/v1/me", headers=headers)
    assert still_valid.status_code == 200


def test_session_delete_rejects_expired_other_session_without_invalidating_caller(auth_client):
    from datetime import datetime, timedelta, timezone
    from app.database import get_db
    from app.models import AuthSession

    registered = register_account(auth_client, "session-expired@example.com")
    assert registered.status_code == 200
    caller_tokens = registered.json()
    caller_headers = {"Authorization": f"Bearer {caller_tokens['access_token']}"}

    second_login = auth_client.post(
        "/api/v1/auth/token",
        data={
            "username": "session-expired@example.com",
            "password": "LedgerGate-api-test-2026",
        },
    )
    assert second_login.status_code == 200

    listing = auth_client.get("/api/v1/auth/sessions", headers=caller_headers)
    assert listing.status_code == 200
    other_sessions = [item for item in listing.json() if not item["current"]]
    assert len(other_sessions) == 1
    expired_session_id = other_sessions[0]["id"]

    db_generator = app.dependency_overrides[get_db]()
    db = next(db_generator)
    try:
        session = db.query(AuthSession).filter(
            AuthSession.id == expired_session_id,
        ).first()
        assert session is not None
        session.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    finally:
        try:
            next(db_generator)
        except StopIteration:
            pass

    response = auth_client.delete(
        f"/api/v1/auth/sessions/{expired_session_id}",
        headers=caller_headers,
    )
    assert response.status_code == 404

    caller_still_valid = auth_client.get("/api/v1/me", headers=caller_headers)
    assert caller_still_valid.status_code == 200

    remaining = auth_client.get("/api/v1/auth/sessions", headers=caller_headers)
    assert remaining.status_code == 200
    assert len(remaining.json()) == 1
    assert remaining.json()[0]["current"] is True

    expired_headers = {
        "Authorization": f"Bearer {second_login.json()['access_token']}"
    }
    expired_access = auth_client.get("/api/v1/me", headers=expired_headers)
    assert expired_access.status_code == 401


def test_logout_all_revokes_every_access_and_refresh_session(auth_client):
    registered = register_account(auth_client, "logout-all@example.com")
    assert registered.status_code == 200
    first_tokens = registered.json()

    second_login = auth_client.post(
        "/api/v1/auth/token",
        data={
            "username": "logout-all@example.com",
            "password": "LedgerGate-api-test-2026",
        },
    )
    assert second_login.status_code == 200
    second_tokens = second_login.json()

    headers = {"Authorization": f"Bearer {first_tokens['access_token']}"}
    response = auth_client.post("/api/v1/auth/logout-all", headers=headers)
    assert response.status_code == 200

    for token in (first_tokens["access_token"], second_tokens["access_token"]):
        denied = auth_client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert denied.status_code == 401

    for token in (first_tokens["refresh_token"], second_tokens["refresh_token"]):
        denied = auth_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": token},
        )
        assert denied.status_code == 401

def test_session_revocation_is_idempotent(auth_client):
    registered = register_account(auth_client, "session-idempotency@example.com")
    assert registered.status_code == 200
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    second_login = auth_client.post(
        "/api/v1/auth/token",
        data={
            "username": "session-idempotency@example.com",
            "password": "LedgerGate-api-test-2026",
        },
    )
    assert second_login.status_code == 200
    sessions = auth_client.get("/api/v1/auth/sessions", headers=headers).json()
    target = next(item for item in sessions if not item["current"])
    request_headers = {**headers, "Idempotency-Key": "revoke-session-0001"}

    first = auth_client.delete(
        f"/api/v1/auth/sessions/{target['id']}",
        headers=request_headers,
    )
    replay = auth_client.delete(
        f"/api/v1/auth/sessions/{target['id']}",
        headers=request_headers,
    )

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json() == {"revoked": True}
    different_request = auth_client.delete(
        f"/api/v1/auth/sessions/{target['id']}",
        headers={**headers, "Idempotency-Key": "revoke-session-0002"},
    )
    assert different_request.status_code == 404
