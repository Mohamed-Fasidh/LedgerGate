"""PostgreSQL integration coverage for authentication-session tenant isolation."""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.database import SessionLocal, set_tenant_context


@pytest.mark.skipif(
    not os.getenv("RLS_TEST_ADMIN_DATABASE_URL"),
    reason="PostgreSQL auth RLS test requires RLS_TEST_ADMIN_DATABASE_URL",
)
def test_postgres_auth_sessions_reject_cross_tenant_reads_and_writes():
    admin_engine = create_engine(os.environ["RLS_TEST_ADMIN_DATABASE_URL"])
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    email_a = f"auth-rls-a-{tenant_a}@example.test"
    email_b = f"auth-rls-b-{tenant_b}@example.test"
    jti_a, jti_b = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    try:
        with admin_engine.connect() as conn:
            relation = conn.execute(text("""
                SELECT c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema()
                  AND c.relname = 'auth_sessions'
            """)).one()
            assert relation.relrowsecurity is True
            assert relation.relforcerowsecurity is True

        with admin_engine.begin() as conn:
            users = conn.execute(text("""
                INSERT INTO users (
                    tenant_id, tenant_name, email, hashed_password, role,
                    is_active, mfa_enabled, created_at
                )
                VALUES
                    (:tenant_a, 'Auth RLS A', :email_a, 'test-hash', 'analyst',
                     TRUE, FALSE, :now),
                    (:tenant_b, 'Auth RLS B', :email_b, 'test-hash', 'analyst',
                     TRUE, FALSE, :now)
                RETURNING id, tenant_id
            """), {
                "tenant_a": tenant_a, "tenant_b": tenant_b,
                "email_a": email_a, "email_b": email_b, "now": now,
            }).all()
            user_ids = {row.tenant_id: row.id for row in users}
            conn.execute(text("""
                INSERT INTO auth_sessions (
                    jti, user_id, tenant_id, created_at, expires_at
                )
                VALUES
                    (:jti_a, :user_a, :tenant_a, :now, :expires),
                    (:jti_b, :user_b, :tenant_b, :now, :expires)
            """), {
                "jti_a": jti_a, "jti_b": jti_b,
                "user_a": user_ids[tenant_a], "user_b": user_ids[tenant_b],
                "tenant_a": tenant_a, "tenant_b": tenant_b,
                "now": now, "expires": now + timedelta(hours=1),
            })

        with SessionLocal() as db:
            # No tenant context must reveal no session rows.
            assert db.execute(
                text("SELECT jti FROM auth_sessions WHERE jti IN (:a, :b)"),
                {"a": jti_a, "b": jti_b},
            ).scalars().all() == []
            db.rollback()

            set_tenant_context(db, tenant_a)
            visible = db.execute(
                text("SELECT jti FROM auth_sessions WHERE jti IN (:a, :b)"),
                {"a": jti_a, "b": jti_b},
            ).scalars().all()
            assert visible == [jti_a]

            # A cross-tenant update must not affect tenant B's session.
            db.execute(
                text("UPDATE auth_sessions SET revoked_at = :now WHERE jti = :jti_b"),
                {"now": now, "jti_b": jti_b},
            )
            db.commit()

            with admin_engine.connect() as conn:
                assert conn.execute(
                    text("SELECT revoked_at FROM auth_sessions WHERE jti = :jti"),
                    {"jti": jti_b},
                ).scalar_one() is None

            set_tenant_context(db, tenant_a)
            with pytest.raises(DBAPIError):
                db.execute(text("""
                    INSERT INTO auth_sessions (
                        jti, user_id, tenant_id, created_at, expires_at
                    )
                    VALUES (:jti, :user_id, :tenant_b, :now, :expires)
                """), {
                    "jti": str(uuid.uuid4()), "user_id": user_ids[tenant_b],
                    "tenant_b": tenant_b, "now": now,
                    "expires": now + timedelta(hours=1),
                })
            db.rollback()
    finally:
        with admin_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM auth_sessions WHERE tenant_id IN (:a, :b)"),
                {"a": tenant_a, "b": tenant_b},
            )
            conn.execute(
                text("DELETE FROM users WHERE tenant_id IN (:a, :b)"),
                {"a": tenant_a, "b": tenant_b},
            )
        admin_engine.dispose()
