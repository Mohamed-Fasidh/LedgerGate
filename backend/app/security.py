from datetime import datetime, timedelta, timezone

from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

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


def create_access_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    claims = {
        "sub": str(user.id),
        "tenant": user.tenant_id,
        "role": user.role.value,
        "iat": datetime.now(timezone.utc),
        "exp": expire,
    }
    return jwt.encode(claims, settings.jwt_secret_key, algorithm="HS256")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
        user_id = int(payload["sub"])
        tenant = payload["tenant"]
        if not isinstance(tenant, str) or not tenant:
            raise ValueError("Invalid tenant claim")
    except (JWTError, KeyError, ValueError, TypeError):
        raise error

    user = (
        db.query(User)
        .filter(User.id == user_id, User.tenant_id == tenant, User.is_active.is_(True))
        .first()
    )
    if not user:
        raise error
    return user


def require_roles(*roles: str):
    def checker(user: User = Depends(get_current_user)):
        if user.role.value not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user

    return checker
