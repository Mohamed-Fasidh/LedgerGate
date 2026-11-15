import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture
def upload_client():
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
            response = client.post(
                "/api/v1/auth/register",
                json={
                    "email": "upload-admin@example.com",
                    "password": "LedgerGate-upload-test-2026",
                    "tenant_name": "Upload Test Workspace",
                },
            )
            assert response.status_code == 200
            headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
            yield client, headers
    finally:
        app.dependency_overrides.pop(get_db, None)
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_upload_rejects_unsupported_file_extension(upload_client):
    client, headers = upload_client

    response = client.post(
        "/api/v1/invoices/upload",
        headers=headers,
        files={"file": ("malware.exe", b"not a document", "application/octet-stream")},
    )

    assert response.status_code == 415


def test_upload_rejects_empty_file(upload_client):
    client, headers = upload_client

    response = client.post(
        "/api/v1/invoices/upload",
        headers=headers,
        files={"file": ("empty.txt", b"", "text/plain")},
    )

    assert response.status_code == 400


def test_upload_rejects_pdf_without_pdf_signature(upload_client):
    client, headers = upload_client

    response = client.post(
        "/api/v1/invoices/upload",
        headers=headers,
        files={"file": ("fake.pdf", b"this is not a PDF", "application/pdf")},
    )

    assert response.status_code == 415


def test_upload_rejects_binary_text_file(upload_client):
    client, headers = upload_client

    response = client.post(
        "/api/v1/invoices/upload",
        headers=headers,
        files={"file": ("binary.txt", b"invoice\\x00data", "text/plain")},
    )

    assert response.status_code == 415
