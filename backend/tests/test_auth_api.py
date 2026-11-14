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
