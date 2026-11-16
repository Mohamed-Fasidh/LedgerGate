import pytest

from app import mcp_server


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://ledgergate.example.com", "https://ledgergate.example.com"),
        ("https://ledgergate.example.com:8443/", "https://ledgergate.example.com:8443"),
        ("http://localhost:8000", "http://localhost:8000"),
        ("http://127.0.0.1:8000/", "http://127.0.0.1:8000"),
        ("http://[::1]:8000", "http://[::1]:8000"),
    ],
)
def test_validate_api_url_accepts_tls_and_local_loopback(url, expected):
    assert mcp_server.validate_api_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        " https://ledgergate.example.com",
        "http://ledgergate.example.com",
        "http://10.0.0.4:8000",
        "http://192.168.1.20:8000",
        "ftp://ledgergate.example.com",
        "https://user:password@ledgergate.example.com",
        "https://ledgergate.example.com/api",
        "https://ledgergate.example.com?token=secret",
        "https://ledgergate.example.com#fragment",
        "https://ledgergate.example.com:99999",
        "https://",
    ],
)
def test_validate_api_url_rejects_unsafe_or_malformed_origins(url):
    with pytest.raises(ValueError):
        mcp_server.validate_api_url(url)


def test_authenticated_mcp_api_calls_do_not_follow_redirects(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    def fake_request(method, url, **kwargs):
        captured.update(method=method, url=url, **kwargs)
        return Response()

    monkeypatch.setattr(mcp_server, "API", "https://ledgergate.example.com")
    monkeypatch.setattr(mcp_server, "TOKEN", "user-scoped-test-token")
    monkeypatch.setattr(mcp_server.httpx, "request", fake_request)

    assert mcp_server.call_api("GET", "/api/v1/invoices") == {"ok": True}
    assert captured["url"] == "https://ledgergate.example.com/api/v1/invoices"
    assert captured["headers"]["Authorization"] == "Bearer user-scoped-test-token"
    assert captured["follow_redirects"] is False
    assert captured["timeout"] == 15
