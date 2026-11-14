from fastapi.testclient import TestClient
from app.main import app
client=TestClient(app)
def test_health():
    response = client.get("/health")
    assert response.json()["status"]=="ok"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_api_responses_receive_restrictive_content_policy_without_breaking_docs():
    api_response = client.get("/api/v1/invoices/1")
    assert api_response.headers["content-security-policy"] == "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    assert "content-security-policy" not in client.get("/docs").headers
