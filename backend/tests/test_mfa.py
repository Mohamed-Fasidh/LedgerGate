import base64
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from cryptography.fernet import Fernet

from app.config import settings
import app.main as main
from app.security import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_totp_secret,
    matching_totp_step,
)


def test_totp_matches_rfc6238_sha1_six_digit_vector():
    # RFC 6238 test secret, timestamp 59 seconds, truncated to six digits.
    secret = base64.b32encode(b"12345678901234567890").decode("ascii").rstrip("=")
    assert matching_totp_step(secret, "287082", now=59) == 1


def test_totp_rejects_replayed_time_step():
    secret = base64.b32encode(b"12345678901234567890").decode("ascii").rstrip("=")
    assert matching_totp_step(secret, "287082", now=59, last_used_step=1) is None


def test_totp_rejects_malformed_codes_and_secrets():
    secret = base64.b32encode(b"12345678901234567890").decode("ascii").rstrip("=")
    assert matching_totp_step(secret, "12345", now=59) is None
    assert matching_totp_step(secret, "12a456", now=59) is None
    assert matching_totp_step("not a base32 secret", "287082", now=59) is None


def test_totp_secret_is_random_and_encrypted_at_rest(monkeypatch):
    monkeypatch.setattr(settings, "mfa_encryption_key", Fernet.generate_key().decode("ascii"))
    first = generate_totp_secret()
    second = generate_totp_secret()
    assert first != second
    encrypted = encrypt_totp_secret(first)
    assert first not in encrypted
    assert decrypt_totp_secret(encrypted) == first


def test_totp_encryption_requires_a_configured_key(monkeypatch):
    monkeypatch.setattr(settings, "mfa_encryption_key", "")
    with pytest.raises(RuntimeError, match="MFA_ENCRYPTION_KEY"):
        encrypt_totp_secret(generate_totp_secret())


def test_mfa_recovery_codes_are_hashed_and_single_use():
    from app.security import (
        consume_mfa_recovery_code,
        generate_mfa_recovery_codes,
        hash_mfa_recovery_code,
    )

    codes = generate_mfa_recovery_codes()
    assert len(codes) == 10
    assert len(set(codes)) == 10
    assert all(len(code) == 24 for code in codes)
    hashes = [hash_mfa_recovery_code(code) for code in codes]
    assert codes[0] not in hashes
    remaining = consume_mfa_recovery_code(hashes, codes[0].lower())
    assert remaining == hashes[1:]
    assert consume_mfa_recovery_code(remaining, codes[0]) is None
    assert consume_mfa_recovery_code(remaining, "not-a-valid-code") is None



class _AttemptQuery:
    def __init__(self, count=0, oldest=None):
        self.failure_count = count
        self.oldest = oldest or datetime.now(timezone.utc)
    def filter(self, *conditions):
        return self
    def count(self):
        return self.failure_count
    def order_by(self, *ordering):
        return self
    def first(self):
        return SimpleNamespace(attempted_at=self.oldest)


class _AttemptDB:
    def __init__(self, count=0):
        self.query_obj = _AttemptQuery(count)
        self.added = []
        self.rolled_back = False
    def query(self, model):
        assert model is main.LoginAttempt
        return self.query_obj
    def add(self, item):
        self.added.append(item)
    def rollback(self):
        self.rolled_back = True


def test_sensitive_mfa_authentication_uses_shared_login_throttle(monkeypatch):
    db = _AttemptDB(count=10)
    user = SimpleNamespace(email="member@example.com")
    request = SimpleNamespace(client=SimpleNamespace(host="192.0.2.10"))
    monkeypatch.setattr(main, "_lock_login_attempt_keys", lambda *args: None)

    with pytest.raises(HTTPException) as error:
        main._begin_sensitive_auth_attempt(db, user, request)

    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"]
    assert db.rolled_back


def test_sensitive_mfa_attempts_store_only_keyed_identifiers(monkeypatch):
    db = _AttemptDB()
    user = SimpleNamespace(email="member@example.com")
    request = SimpleNamespace(client=SimpleNamespace(host="192.0.2.10"))
    monkeypatch.setattr(main, "_lock_login_attempt_keys", lambda *args: None)

    email_hash, ip_hash, attempted_at = main._begin_sensitive_auth_attempt(db, user, request)
    main._record_sensitive_auth_attempt(db, email_hash, ip_hash, False, attempted_at)

    event = db.added[0]
    assert event.email_hash == email_hash and len(email_hash) == 64
    assert event.ip_hash == ip_hash and len(ip_hash) == 64
    assert "member@example.com" not in event.email_hash
    assert "192.0.2.10" not in event.ip_hash
    assert event.succeeded is False
