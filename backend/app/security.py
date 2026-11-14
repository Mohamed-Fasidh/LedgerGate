from datetime import datetime, timedelta, timezone
import hashlib
import uuid

from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.config import settings
from app.database import get_db, set_tenant_context
from app.models import User, AuthSession, LoginThrottle

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


def validate_password(password: str) -> None:
    encoded_length = len(password.encode("utf-8"))
    if len(password) < 12 or encoded_length > 72:
        raise ValueError("Password must be at least 12 characters and no more than 72 UTF-8 bytes")


def hash_password(password: str) -> str:
    validate_password(password)
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        validate_password(plain)
        return pwd_context.verify(plain, hashed)
    except (ValueError, TypeError):
        return False


def create_access_token(user: User, db: Session) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)
    jti = str(uuid.uuid4())
    db.add(AuthSession(jti=jti, user_id=user.id, tenant_id=user.tenant_id, created_at=now, expires_at=expire))
    db.flush()
    claims = {"sub": str(user.id), "tenant": user.tenant_id, "role": user.role.value,
              "jti": jti, "iat": now, "exp": expire}
    return jwt.encode(claims, settings.jwt_secret_key, algorithm="HS256")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def login_email_key(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def _lock_login_key(db: Session, key: str) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:login_key))"), {"login_key": key})


def login_is_locked(db: Session, email: str) -> bool:
    _lock_login_key(db, login_email_key(email))
    row = db.query(LoginThrottle).filter(LoginThrottle.email_key == login_email_key(email)).with_for_update().first()
    return bool(row and row.locked_until and _utc(row.locked_until) > datetime.now(timezone.utc))


def record_login_failure(db: Session, email: str) -> None:
    now = datetime.now(timezone.utc)
    key = login_email_key(email)
    _lock_login_key(db, key)
    row = db.query(LoginThrottle).filter(LoginThrottle.email_key == key).with_for_update().first()
    if row is None:
        row = LoginThrottle(email_key=key, failed_attempts=0, window_started_at=now, updated_at=now)
        db.add(row)
        db.flush()
    if (now - _utc(row.window_started_at)).total_seconds() >= 900:
        row.failed_attempts, row.window_started_at, row.locked_until = 0, now, None
    row.failed_attempts += 1
    row.updated_at = now
    if row.failed_attempts >= 5:
        row.locked_until = now + timedelta(minutes=15)


def clear_login_failures(db: Session, email: str) -> None:
    _lock_login_key(db, login_email_key(email))
    row = db.query(LoginThrottle).filter(LoginThrottle.email_key == login_email_key(email)).with_for_update().first()
    if row:
        now = datetime.now(timezone.utc)
        row.failed_attempts, row.window_started_at, row.locked_until, row.updated_at = 0, now, None, now


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    error = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired credentials",
                          headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
        user_id, tenant, jti = int(payload["sub"]), payload["tenant"], payload["jti"]
        if not isinstance(tenant, str) or not tenant or not isinstance(jti, str):
            raise ValueError("Invalid token claims")
    except (JWTError, KeyError, ValueError, TypeError):
        raise error
    # Establish tenant context before querying the RLS-protected session registry.
    set_tenant_context(db, tenant)
    session = db.query(AuthSession).filter(
        AuthSession.jti == jti, AuthSession.user_id == user_id, AuthSession.tenant_id == tenant,
        AuthSession.revoked_at.is_(None)
    ).first()
    if not session or _utc(session.expires_at) <= datetime.now(timezone.utc):
        raise error
    user = db.query(User).filter(User.id == user_id, User.tenant_id == tenant, User.is_active.is_(True)).first()
    if not user:
        raise error
    set_tenant_context(db, user.tenant_id)
    return user


def require_roles(*roles: str):
    def checker(user: User = Depends(get_current_user)):
        if user.role.value not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return checker


# TOTP MFA helpers (RFC 6238, SHA-1, 30-second steps).
import base64
import hmac
import secrets
import struct
import time
from urllib.parse import quote
from cryptography.fernet import Fernet, InvalidToken


def _mfa_fernet() -> Fernet:
    if not settings.app_encryption_key:
        raise RuntimeError("MFA encryption is not configured")
    try:
        return Fernet(settings.app_encryption_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("MFA encryption is not configured correctly") from exc


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def encrypt_totp_secret(secret: str) -> str:
    return _mfa_fernet().encrypt(secret.encode("ascii")).decode("ascii")


def decrypt_totp_secret(encrypted: str) -> str:
    try:
        return _mfa_fernet().decrypt(encrypted.encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise ValueError("Stored MFA secret cannot be decrypted") from exc


def totp_code(secret: str, timestamp: int | None = None) -> str:
    counter = int((time.time() if timestamp is None else timestamp) // 30)
    padded = secret.upper() + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f"{binary % 1000000:06d}"


def verify_totp(secret: str, code: str, timestamp: int | None = None) -> bool:
    if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
        return False
    now = int(time.time() if timestamp is None else timestamp)
    return any(hmac.compare_digest(totp_code(secret, now + drift * 30), code) for drift in (-1, 0, 1))


def totp_provisioning_uri(secret: str, email: str, issuer: str = "LedgerGate") -> str:
    label = quote(f"{issuer}:{email}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


# Opaque, one-time refresh tokens are stored only as SHA-256 hashes.
import secrets


def issue_refresh_token(db: Session, user: User) -> str:
    # The tenant prefix lets the refresh endpoint establish transaction-local
    # RLS context before looking up the hash. The random suffix remains secret.
    if not user.tenant_id or "." in user.tenant_id:
        raise RuntimeError("Invalid tenant identifier for refresh token")
    token = f"{user.tenant_id}.{secrets.token_urlsafe(48)}"
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    session = (
        db.query(AuthSession)
        .filter(AuthSession.user_id == user.id, AuthSession.tenant_id == user.tenant_id,
                AuthSession.revoked_at.is_(None), AuthSession.refresh_token_hash.is_(None))
        .order_by(AuthSession.id.desc())
        .with_for_update()
        .first()
    )
    if session is None:
        raise RuntimeError("An active access session is required")
    session.refresh_token_hash = token_hash
    session.refresh_expires_at = datetime.now(timezone.utc) + timedelta(days=14)
    db.flush()
    return token


def rotate_refresh_token(db: Session, raw_token: str) -> tuple[User, str, str]:
    if not isinstance(raw_token, str) or len(raw_token) < 32 or len(raw_token) > 256:
        raise ValueError("Invalid refresh token")
    tenant_id, separator, secret = raw_token.partition(".")
    if (not separator or not tenant_id or len(tenant_id) > 64
            or not secret or "." in secret):
        raise ValueError("Invalid refresh token")
    try:
        raw_token.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("Invalid refresh token") from exc
    # AuthSession is protected by FORCE ROW LEVEL SECURITY. Set context
    # from the untrusted tenant prefix before querying; the unguessable token
    # hash is still required to match a stored session within that tenant.
    set_tenant_context(db, tenant_id)
    token_hash = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
    session = (
        db.query(AuthSession)
        .filter(AuthSession.refresh_token_hash == token_hash,
                AuthSession.tenant_id == tenant_id)
        .with_for_update()
        .first()
    )
    now = datetime.now(timezone.utc)
    if (session is None or session.revoked_at is not None or session.refresh_expires_at is None
            or _utc(session.refresh_expires_at) <= now):
        raise ValueError("Invalid refresh token")
    # One-time use: invalidate the old refresh and its associated access token.
    session.revoked_at = now
    session.refresh_token_hash = None
    user = db.query(User).filter(
        User.id == session.user_id, User.tenant_id == session.tenant_id, User.is_active.is_(True)
    ).first()
    if user is None:
        raise ValueError("Invalid refresh token")
    access = create_access_token(user, db)
    refresh = issue_refresh_token(db, user)
    return user, access, refresh



def revoke_current_session(db: Session, user: User, access_token: str) -> None:
    """Revoke the authenticated access session and its paired refresh token."""
    try:
        payload = jwt.decode(access_token, settings.jwt_secret_key, algorithms=["HS256"])
        if (int(payload.get("sub", -1)) != user.id
                or payload.get("tenant") != user.tenant_id
                or not isinstance(payload.get("jti"), str)):
            raise ValueError("Invalid session")
    except (JWTError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid session") from exc
    set_tenant_context(db, user.tenant_id)
    session = (db.query(AuthSession).filter(
        AuthSession.jti == payload["jti"], AuthSession.user_id == user.id,
        AuthSession.tenant_id == user.tenant_id
    ).with_for_update().first())
    if session is None or session.revoked_at is not None:
        raise ValueError("Invalid session")
    session.revoked_at = datetime.now(timezone.utc)
    session.refresh_token_hash = None
    session.refresh_expires_at = None
    db.flush()
