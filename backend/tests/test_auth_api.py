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
        data={"password": "LedgerGate-api-test-2026", "code": security.totp_code(secret)},
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
