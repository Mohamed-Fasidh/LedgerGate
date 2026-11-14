import pytest
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AuthSession, LoginThrottle, Role, User
from app.security import (
    clear_login_failures,
    create_access_token,
    get_current_user,
    hash_password,
    login_email_key,
    login_is_locked,
    record_login_failure,
    validate_password,
    verify_password,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def make_user(db):
    user = User(
        tenant_id="tenant-test",
        tenant_name="Test Workspace",
        email="security-test@example.com",
        hashed_password=hash_password("LedgerGate-test-password-2026"),
        role=Role.admin,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


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


def test_login_throttle_locks_after_five_failures_and_clears_on_success(db_session):
    email = "LOCKOUT@example.com"
    assert not login_is_locked(db_session, email)
    for _ in range(5):
        record_login_failure(db_session, email)
        db_session.commit()

    assert login_is_locked(db_session, email)
    row = db_session.query(LoginThrottle).filter_by(email_key=login_email_key(email)).one()
    assert row.failed_attempts == 5
    assert row.locked_until is not None

    clear_login_failures(db_session, email)
    db_session.commit()
    assert not login_is_locked(db_session, email)
    row = db_session.query(LoginThrottle).filter_by(email_key=login_email_key(email)).one()
    assert row.failed_attempts == 0
    assert row.locked_until is None


def test_login_throttle_lock_expires(db_session):
    email = "expired-lock@example.com"
    for _ in range(5):
        record_login_failure(db_session, email)
        db_session.commit()

    row = db_session.query(LoginThrottle).filter_by(email_key=login_email_key(email)).one()
    row.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()
    assert not login_is_locked(db_session, email)


def test_access_token_creates_persisted_session_and_authenticates_user(db_session):
    user = make_user(db_session)
    token = create_access_token(user, db_session)
    db_session.commit()

    session = db_session.query(AuthSession).one()
    assert session.user_id == user.id
    assert session.tenant_id == user.tenant_id
    assert session.jti
    assert session.revoked_at is None
    assert get_current_user(token=token, db=db_session).id == user.id


def test_revoked_access_token_is_rejected(db_session):
    user = make_user(db_session)
    token = create_access_token(user, db_session)
    db_session.commit()

    session = db_session.query(AuthSession).one()
    session.revoked_at = datetime.now(timezone.utc)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        get_current_user(token=token, db=db_session)
    assert exc.value.status_code == 401


def test_totp_matches_rfc6238_six_digit_vector():
    from app.security import totp_code, verify_totp
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp_code(secret, 59) == "287082"
    assert verify_totp(secret, "287082", 59)
    assert not verify_totp(secret, "000000", 59)


def test_totp_rejects_malformed_codes():
    from app.security import verify_totp
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert not verify_totp(secret, "12345", 59)
    assert not verify_totp(secret, "12A456", 59)


def test_totp_secret_encryption_round_trip(monkeypatch):
    from app import security
    monkeypatch.setattr(security.settings, "app_encryption_key", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
    secret = security.generate_totp_secret()
    encrypted = security.encrypt_totp_secret(secret)
    assert encrypted != secret
    assert security.decrypt_totp_secret(encrypted) == secret


def test_totp_provisioning_uri_contains_issuer_and_secret():
    from app.security import totp_provisioning_uri
    uri = totp_provisioning_uri("ABC123", "user@example.com")
    assert uri.startswith("otpauth://totp/")
    assert "secret=ABC123" in uri
    assert "issuer=LedgerGate" in uri


def test_refresh_token_is_hashed_and_rotates_once(db_session):
    import hashlib
    from app.security import issue_refresh_token, rotate_refresh_token

    user = make_user(db_session)
    access = create_access_token(user, db_session)
    refresh = issue_refresh_token(db_session, user)
    db_session.commit()

    old_session = db_session.query(AuthSession).one()
    assert old_session.refresh_token_hash == hashlib.sha256(refresh.encode()).hexdigest()
    assert old_session.refresh_token_hash != refresh

    rotated_user, new_access, new_refresh = rotate_refresh_token(db_session, refresh)
    db_session.commit()
    assert rotated_user.id == user.id
    assert new_access != access
    assert new_refresh != refresh
    assert db_session.query(AuthSession).filter(AuthSession.revoked_at.is_not(None)).count() == 1

    with pytest.raises(ValueError):
        rotate_refresh_token(db_session, refresh)


def test_refresh_token_rejects_unknown_and_malformed_values(db_session):
    from app.security import rotate_refresh_token
    with pytest.raises(ValueError):
        rotate_refresh_token(db_session, "short")
    with pytest.raises(ValueError):
        rotate_refresh_token(db_session, "x" * 64)


def test_refresh_token_cannot_be_used_after_logout_all(db_session):
    from app.security import issue_refresh_token, rotate_refresh_token

    user = make_user(db_session)
    create_access_token(user, db_session)
    refresh = issue_refresh_token(db_session, user)
    db_session.commit()
    session = db_session.query(AuthSession).one()
    session.revoked_at = datetime.now(timezone.utc)
    db_session.commit()
    with pytest.raises(ValueError):
        rotate_refresh_token(db_session, refresh)
