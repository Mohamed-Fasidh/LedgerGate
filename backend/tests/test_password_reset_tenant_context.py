"""Regression checks for password recovery's tenant context transition.

The reset token is resolved before the tenant is known. Once resolved, all
subsequent session operations must use the token's tenant for PostgreSQL RLS.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AuthSession, PasswordResetToken, Role, User
from app.security import hash_password, reset_password_with_token
import hashlib


def test_password_reset_sets_token_tenant_before_session_revocation():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    try:
        with factory() as db:
            user = User(
                tenant_id="tenant-recovery",
                tenant_name="Recovery Workspace",
                email="recovery-context@example.test",
                hashed_password=hash_password("Original-password-2026"),
                role=Role.admin,
                is_active=True,
            )
            db.add(user)
            db.flush()
            raw_token = "test-reset-token-tenant-context"
            db.add(PasswordResetToken(
                tenant_id=user.tenant_id,
                user_id=user.id,
                token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            ))
            db.add(AuthSession(
                tenant_id=user.tenant_id,
                user_id=user.id,
                jti="recovery-context-jti",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ))
            db.commit()

            # SQLite cannot enforce PostgreSQL RLS. Assert the context is
            # established while executing the real password-reset function.
            with patch("app.security.set_tenant_context") as set_context:
                updated = reset_password_with_token(db, raw_token, "Replacement-password-2026")
                set_context.assert_called_once_with(db, "tenant-recovery")
                assert updated.id == user.id
                assert db.query(AuthSession).one().revoked_at is not None
                db.commit()
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
