import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Role, User


@pytest.fixture
def policy_client():
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
            yield client, factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def register_policy_admin(client, email):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "LedgerGate-policy-test-2026",
            "tenant_name": f"{email} workspace",
        },
    )
    assert response.status_code == 200
    tokens = response.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_admin_can_create_currency_policy_and_idempotent_replay(policy_client):
    client, _ = policy_client
    headers = register_policy_admin(client, "policy-admin@example.com")
    headers["Idempotency-Key"] = "policy-usd-version-one"

    payload = {
        "currency": "usd",
        "threshold_amount": 50000,
        "required_approvals": 2,
    }
    first = client.post("/api/v1/approval-policies", headers=headers, json=payload)
    replay = client.post("/api/v1/approval-policies", headers=headers, json=payload)

    assert first.status_code == 200
    assert first.json()["currency"] == "USD"
    assert first.json()["version"] == 1
    assert replay.status_code == 200
    assert replay.json() == first.json()


def test_approval_policy_creation_requires_admin_role(policy_client):
    client, factory = policy_client
    headers = register_policy_admin(client, "policy-analyst@example.com")

    db = factory()
    try:
        user = db.query(User).filter_by(email="policy-analyst@example.com").one()
        user.role = Role.analyst
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/api/v1/approval-policies",
        headers=headers,
        json={
            "currency": "INR",
            "threshold_amount": 100000,
            "required_approvals": 2,
        },
    )
    assert response.status_code == 403


def test_policy_versions_are_isolated_between_tenants(policy_client):
    client, _ = policy_client
    first_headers = register_policy_admin(client, "policy-tenant-one@example.com")
    second_headers = register_policy_admin(client, "policy-tenant-two@example.com")
    payload = {
        "currency": "USD",
        "threshold_amount": 50000,
        "required_approvals": 2,
    }

    first = client.post("/api/v1/approval-policies", headers=first_headers, json=payload)
    second = client.post("/api/v1/approval-policies", headers=second_headers, json=payload)

    assert first.status_code == second.status_code == 200
    assert first.json()["version"] == 1
    assert second.json()["version"] == 1
    assert first.json()["id"] != second.json()["id"]
