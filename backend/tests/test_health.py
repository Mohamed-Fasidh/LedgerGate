from contextlib import nullcontext

from fastapi.testclient import TestClient

from app import main
from app.main import app

client = TestClient(app)


class FakeDB:
    def __init__(self, fail=False):
        self.fail = fail

    def execute(self, statement):
        if self.fail:
            raise RuntimeError("database unavailable")
        return 1


def install_db(monkeypatch, db):
    def override_get_db():
        yield db
    monkeypatch.setitem(app.dependency_overrides, main.get_db, override_get_db)


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_readiness_reports_database_and_scanner(monkeypatch):
    install_db(monkeypatch, FakeDB())
    monkeypatch.setattr(main.settings, "app_env", "development")
    monkeypatch.setattr(main.settings, "clamav_host", "clamav.test")
    monkeypatch.setattr(main.settings, "clamav_port", 3310)
    monkeypatch.setattr(main.socket, "create_connection", lambda address, timeout: nullcontext())
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {"database": "ok", "clamav": "reachable"},
    }


def test_readiness_returns_503_when_database_is_unavailable(monkeypatch):
    install_db(monkeypatch, FakeDB(fail=True))
    monkeypatch.setattr(main.settings, "app_env", "development")
    monkeypatch.setattr(main.settings, "clamav_host", "")
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["dependencies"]["database"] == "unavailable"


def test_readiness_returns_503_when_configured_scanner_is_unavailable(monkeypatch):
    install_db(monkeypatch, FakeDB())
    monkeypatch.setattr(main.settings, "app_env", "development")
    monkeypatch.setattr(main.settings, "clamav_host", "clamav.test")
    monkeypatch.setattr(main.settings, "clamav_port", 3310)

    def fail_connection(address, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(main.socket, "create_connection", fail_connection)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["dependencies"]["clamav"] == "unavailable"


def test_readiness_allows_unconfigured_scanner_in_development(monkeypatch):
    install_db(monkeypatch, FakeDB())
    monkeypatch.setattr(main.settings, "app_env", "development")
    monkeypatch.setattr(main.settings, "clamav_host", "")
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["dependencies"]["clamav"] == "not_configured"
