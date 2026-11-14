import pytest

from app.security import hash_password, validate_password, verify_password


def test_password_requires_minimum_length():
    with pytest.raises(ValueError):
        validate_password("short")


def test_password_rejects_more_than_bcrypt_byte_limit():
    with pytest.raises(ValueError):
        validate_password("é" * 37)


def test_password_hash_and_verify_round_trip():
    password = "LedgerGate-test-password-2026"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed)
    assert not verify_password("a-different-password", hashed)


def test_verify_password_fails_closed_for_invalid_input():
    assert verify_password("short", "not-a-valid-hash") is False


def test_registration_rejects_password_over_bcrypt_utf8_byte_limit():
    from pydantic import ValidationError
    from app.schemas import RegisterIn

    with pytest.raises(ValidationError):
        RegisterIn(email="user@example.com", password="é" * 37, tenant_name="Example")


def test_registration_accepts_password_within_bcrypt_utf8_byte_limit():
    from app.schemas import RegisterIn

    payload = RegisterIn(
        email="user@example.com", password="LedgerGate-test-password-2026", tenant_name="Example"
    )
    assert payload.email == "user@example.com"
