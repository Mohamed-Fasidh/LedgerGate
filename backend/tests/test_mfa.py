import base64

import pytest
from cryptography.fernet import Fernet

from app.config import settings
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
