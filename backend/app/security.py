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
