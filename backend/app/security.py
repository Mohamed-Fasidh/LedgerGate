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
