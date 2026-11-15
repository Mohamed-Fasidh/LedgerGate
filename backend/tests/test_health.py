from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.json()["status"] == "ok"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["permissions-policy"] == "camera=(), geolocation=(), microphone=()"
    assert response.headers["cross-origin-opener-policy"] == "same-origin"


def test_api_responses_receive_restrictive_content_policy_without_breaking_docs():
    api_response = client.get("/api/v1/invoices/1")
    assert api_response.headers["content-security-policy"] == "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    assert "content-security-policy" not in client.get("/docs").headers


def test_auth_responses_are_not_cached():
    response = client.post("/api/v1/auth/token", data={})
    assert response.headers["cache-control"] == "no-store"


def test_hsts_is_added_only_in_production(monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, "app_env", "production")
    production_response = client.get("/health")
    assert production_response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"

    monkeypatch.setattr(main.settings, "app_env", "test")
    non_production_response = client.get("/health")
    assert "strict-transport-security" not in non_production_response.headers


def test_readiness_reports_database_available(monkeypatch):
    from app import main

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement):
            assert str(statement) == "SELECT 1"

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    monkeypatch.setattr(main, "engine", FakeEngine())
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "service": "ledgergate-api"}


def test_readiness_fails_closed_when_database_is_unavailable(monkeypatch):
    from app import main

    class FailedEngine:
        def connect(self):
            raise OSError("database credentials must not leak")

    monkeypatch.setattr(main, "engine", FailedEngine())
    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Service dependencies are unavailable"}
    assert "credentials" not in response.text
