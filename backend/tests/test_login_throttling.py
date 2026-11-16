import re

from app.config import settings
from app.main import _login_attempt_hash, _registration_lock_key


def test_login_throttle_identifiers_are_keyed_and_not_plain_hashes(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "test-secret-one")
    first = _login_attempt_hash("person@example.com")
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    monkeypatch.setattr(settings, "jwt_secret_key", "test-secret-two")
    second = _login_attempt_hash("person@example.com")
    assert first != second
    assert first != __import__("hashlib").sha256(b"person@example.com").hexdigest()


def test_login_throttle_lock_keys_are_stable_signed_bigints():
    digest = _login_attempt_hash("person@example.com")
    key = _registration_lock_key("login-email", digest)
    assert isinstance(key, int)
    assert -(2**63) <= key < 2**63
    assert key == _registration_lock_key("login-email", digest)
    assert key != _registration_lock_key("login-ip", digest)
