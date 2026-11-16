import base64
import hashlib
import hmac
import re
import secrets
import struct
import time
from cryptography.fernet import Fernet, InvalidToken
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(user: User):
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode({"sub": str(user.id), "tenant": user.tenant_id, "role": user.role.value, "token_type": "access", "exp": expire}, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

def create_refresh_token(user: User, token_id: str, family_id: str):
    expire = datetime.now(timezone.utc) + timedelta(days=30)
    token = jwt.encode({
        "sub": str(user.id), "tenant": user.tenant_id, "role": user.role.value,
        "token_type": "refresh", "jti": token_id, "family": family_id, "exp": expire,
    }, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, expire

def decode_refresh_token(token: str):
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    if payload.get("token_type") != "refresh" or not payload.get("jti") or not payload.get("family"):
        raise JWTError("Invalid refresh token")
    return payload

def set_database_tenant(db: Session, tenant_id: str) -> None:
    """Set transaction-connection tenant context consumed by PostgreSQL RLS policies.

    This uses a session-level setting because application endpoints may commit more
    than once in a request. get_db() resets it before the pooled connection is reused.
    """
    if not tenant_id or len(tenant_id) > 64:
        raise ValueError("Invalid tenant context")
    db.execute(text("SELECT set_config('app.tenant_id', :tenant_id, false)"), {"tenant_id": tenant_id})

def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    error = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired credentials", headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("token_type", "access") != "access":
            raise JWTError("Refresh token cannot authenticate API requests")
        user_id, tenant = int(payload["sub"]), payload["tenant"]
    except (JWTError, KeyError, ValueError, TypeError):
        raise error
    user = db.query(User).filter(User.id == user_id, User.tenant_id == tenant, User.is_active.is_(True)).first()
    if not user:
        raise error
    set_database_tenant(db, user.tenant_id)
    return user

def require_roles(*roles):
    def checker(user: User = Depends(get_current_user)):
        if user.role.value not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return checker


def _mfa_fernet() -> Fernet:
    key = settings.mfa_encryption_key.strip()
    if not key:
        raise RuntimeError("MFA_ENCRYPTION_KEY must be configured before enabling MFA")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("MFA_ENCRYPTION_KEY must be a valid Fernet key") from exc


def generate_totp_secret() -> str:
    return base64.b32encode(__import__("secrets").token_bytes(20)).decode("ascii").rstrip("=")


def encrypt_totp_secret(secret: str) -> str:
    return _mfa_fernet().encrypt(secret.encode("ascii")).decode("ascii")


def decrypt_totp_secret(encrypted_secret: str) -> str:
    try:
        return _mfa_fernet().decrypt(encrypted_secret.encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("Stored MFA secret cannot be decrypted; verify MFA_ENCRYPTION_KEY") from exc


def matching_totp_step(secret: str, code: str | None, *, now: float | None = None, last_used_step: int | None = None) -> int | None:
    """Return the matching RFC 6238 time-step, rejecting replayed steps."""
    if not code or not re.fullmatch(r"[0-9]{6}", code):
        return None
    normalized = secret.strip().upper()
    try:
        key = base64.b32decode(normalized + "=" * ((8 - len(normalized) % 8) % 8), casefold=True)
    except (ValueError, TypeError):
        return None
    current_step = int((time.time() if now is None else now) // 30)
    for step in range(current_step - 1, current_step + 2):
        if last_used_step is not None and step <= last_used_step:
            continue
        digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        binary = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
        expected = f"{binary % 1_000_000:06d}"
        if hmac.compare_digest(expected, code):
            return step
    return None


def generate_mfa_recovery_codes(count: int = 10) -> list[str]:
    if count < 1 or count > 20:
        raise ValueError("Recovery-code count must be between 1 and 20")
    return [secrets.token_hex(12).upper() for _ in range(count)]


def hash_mfa_recovery_code(code: str) -> str:
    normalized = re.sub(r"[-\s]", "", code).upper()
    return hashlib.sha256(normalized.encode("ascii", errors="ignore")).hexdigest()


def consume_mfa_recovery_code(hashes: list[str] | None, code: str | None) -> list[str] | None:
    """Return the remaining hashes when one valid code is consumed, else None."""
    if not code or not re.fullmatch(r"[A-Fa-f0-9\-\s]{24,32}", code):
        return None
    candidate = hash_mfa_recovery_code(code)
    remaining = list(hashes or [])
    for index, stored_hash in enumerate(remaining):
        if hmac.compare_digest(candidate, stored_hash):
            del remaining[index]
            return remaining
    return None
