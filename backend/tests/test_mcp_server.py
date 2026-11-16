import pytest

from app.mcp_server import validate_api_url


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
    assert validate_api_url(url) == expected


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
        validate_api_url(url)
