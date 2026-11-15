import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Role, User


@pytest.fixture
def audit_client():
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


def register_audit_user(client, email):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "LedgerGate-audit-test-2026",
            "tenant_name": f"{email} workspace",
        },
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def set_user_role(factory, email, role):
    db = factory()
    try:
        user = db.query(User).filter_by(email=email).one()
        user.role = role
        db.commit()
    finally:
        db.close()


def test_audit_chain_verification_requires_admin_or_reviewer(audit_client):
    client, factory = audit_client
    headers = register_audit_user(client, "audit-analyst@example.com")
    set_user_role(factory, "audit-analyst@example.com", Role.analyst)

    response = client.get("/api/v1/audit-events/verify", headers=headers)

    assert response.status_code == 403


def test_reviewer_can_verify_tenant_audit_chain(audit_client):
    client, factory = audit_client
    headers = register_audit_user(client, "audit-reviewer@example.com")
    set_user_role(factory, "audit-reviewer@example.com", Role.reviewer)

    response = client.get("/api/v1/audit-events/verify", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"valid": True, "first_invalid_event_id": None}


def test_audit_events_support_bounded_pagination(audit_client):
    client, _ = audit_client
    headers = register_audit_user(client, "audit-pagination@example.com")

    response = client.get("/api/v1/audit-events?limit=25&offset=10", headers=headers)
    invalid_limit = client.get("/api/v1/audit-events?limit=501", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 25, "offset": 10}
    assert invalid_limit.status_code == 422


def test_audit_events_are_scoped_to_authenticated_tenant(audit_client):
    client, _ = audit_client
    first_headers = register_audit_user(client, "audit-tenant-one@example.com")
    second_headers = register_audit_user(client, "audit-tenant-two@example.com")

    first_response = client.get("/api/v1/audit-events", headers=first_headers)
    second_response = client.get("/api/v1/audit-events", headers=second_headers)

    assert first_response.status_code == second_response.status_code == 200
    assert first_response.json()["items"] == []
    assert second_response.json()["items"] == []
    assert first_response.json()["total"] == 0
    assert second_response.json()["total"] == 0
