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
