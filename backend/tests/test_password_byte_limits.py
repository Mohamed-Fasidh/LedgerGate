import pytest
from pydantic import ValidationError

from app.schemas import ChangePasswordIn, RegisterIn
from app.security import hash_password, verify_password


def test_registration_rejects_password_over_bcrypt_utf8_byte_limit():
    # 36 characters but 72 UTF-8 bytes: accepted at the exact boundary.
    RegisterIn(email="person@example.com", password="é" * 36, tenant_name="Example")
    with pytest.raises(ValidationError, match="72 UTF-8 bytes"):
        RegisterIn(email="person@example.com", password="é" * 37, tenant_name="Example")


def test_password_change_rejects_oversized_utf8_passwords():
    with pytest.raises(ValidationError, match="72 UTF-8 bytes"):
        ChangePasswordIn(current_password="valid-current-password", new_password="é" * 25)


def test_password_hashing_and_verification_enforce_bcrypt_byte_limit():
    with pytest.raises(ValueError, match="72 UTF-8 bytes"):
        hash_password("é" * 25)
    assert verify_password("é" * 25, "not-a-real-hash") is False
