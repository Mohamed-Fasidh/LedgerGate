import re

from app.config import settings
from types import SimpleNamespace

from app.main import _login_attempt_hash, _login_credentials_are_valid, _registration_lock_key


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



def test_login_rejects_inactive_accounts_even_with_correct_password():
    inactive_user = SimpleNamespace(is_active=False)
    assert not _login_credentials_are_valid(inactive_user, password_valid=True)


def test_login_allows_active_accounts_with_correct_password():
    active_user = SimpleNamespace(is_active=True)
    assert _login_credentials_are_valid(active_user, password_valid=True)


def test_login_rejects_missing_user_or_invalid_password():
    assert not _login_credentials_are_valid(None, password_valid=True)
    assert not _login_credentials_are_valid(SimpleNamespace(is_active=True), password_valid=False)
