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



@pytest.mark.skipif(
    not os.getenv("RLS_TEST_ADMIN_DATABASE_URL"),
    reason="PostgreSQL auth boundary test requires RLS_TEST_ADMIN_DATABASE_URL",
)
def test_password_reset_function_enforces_user_tenant_and_expiry_boundary():
    """The runtime role can invoke reset functions, but cannot mint cross-tenant tokens."""
    admin_engine = create_engine(os.environ["RLS_TEST_ADMIN_DATABASE_URL"])
    tenant_a, tenant_b = str(uuid.uuid4()), str(uuid.uuid4())
    email_a = f"auth-reset-a-{tenant_a}@example.test"
    email_b = f"auth-reset-b-{tenant_b}@example.test"
    token_valid = f"valid-{uuid.uuid4().hex}"
    token_mismatch = f"mismatch-{uuid.uuid4().hex}"
    token_expired = f"expired-{uuid.uuid4().hex}"
    token_inactive = f"inactive-{uuid.uuid4().hex}"
    token_deactivated = f"deactivated-{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)

    try:
        with admin_engine.begin() as conn:
            users = conn.execute(text("""
                INSERT INTO users (
                    tenant_id, tenant_name, email, hashed_password, role,
                    is_active, mfa_enabled, created_at
                )
                VALUES
                    (:tenant_a, 'Reset A', :email_a, 'test-hash', 'analyst',
                     TRUE, FALSE, :now),
                    (:tenant_b, 'Reset B', :email_b, 'test-hash', 'analyst',
                     TRUE, FALSE, :now)
                RETURNING id, tenant_id
            """), {
                "tenant_a": tenant_a, "tenant_b": tenant_b,
                "email_a": email_a, "email_b": email_b, "now": now,
            }).all()
            user_ids = {row.tenant_id: row.id for row in users}

        # The CI role is deliberately not allowed direct access to either
        # sensitive table; all such access must pass through SECURITY DEFINER.
        with SessionLocal() as db:
            privileges = db.execute(text("""
                SELECT
                  has_table_privilege(current_user, 'public.login_throttles', 'SELECT'),
                  has_table_privilege(current_user, 'public.login_throttles', 'INSERT'),
                  has_table_privilege(current_user, 'public.login_throttles', 'UPDATE'),
                  has_table_privilege(current_user, 'public.login_throttles', 'DELETE'),
                  has_table_privilege(current_user, 'public.password_reset_tokens', 'SELECT'),
                  has_table_privilege(current_user, 'public.password_reset_tokens', 'INSERT'),
                  has_table_privilege(current_user, 'public.password_reset_tokens', 'UPDATE'),
                  has_table_privilege(current_user, 'public.password_reset_tokens', 'DELETE')
            """)).one()
            assert tuple(privileges) == (False, False, False, False, False, False, False, False)

            # A valid function call with another tenant's ID must be a silent
            # no-op, avoiding an account-existence oracle.
            db.execute(text("""
                SELECT public.auth_issue_password_reset(
                    :user_id, :wrong_tenant, :token_hash, :expires_at
                )
            """), {
                "user_id": user_ids[tenant_a],
                "wrong_tenant": tenant_b,
                "token_hash": token_mismatch,
                "expires_at": now + timedelta(minutes=10),
            })
            db.commit()

            # A valid user/tenant pair and short-lived expiry is accepted.
            db.execute(text("""
                SELECT public.auth_issue_password_reset(
                    :user_id, :tenant_id, :token_hash, :expires_at
                )
            """), {
                "user_id": user_ids[tenant_a],
                "tenant_id": tenant_a,
                "token_hash": token_valid,
                "expires_at": now + timedelta(minutes=10),
            })
            db.commit()

            # Even a valid user/tenant pair cannot request an excessively
            # long-lived reset credential.
            db.execute(text("""
                SELECT public.auth_issue_password_reset(
                    :user_id, :tenant_id, :token_hash, :expires_at
                )
            """), {
                "user_id": user_ids[tenant_a],
                "tenant_id": tenant_a,
                "token_hash": token_expired,
                "expires_at": now + timedelta(minutes=16),
            })
            db.commit()

            # Reset credentials are atomically consumed at most once.
            consumed = db.execute(text("""
                SELECT user_id, tenant_id
                FROM public.auth_consume_password_reset(:token_hash)
            """), {"token_hash": token_valid}).mappings().first()
            assert consumed is not None
            assert consumed["user_id"] == user_ids[tenant_a]
            assert consumed["tenant_id"] == tenant_a
            db.commit()

            consumed_again = db.execute(text("""
                SELECT user_id, tenant_id
                FROM public.auth_consume_password_reset(:token_hash)
            """), {"token_hash": token_valid}).mappings().first()
            assert consumed_again is None
            db.commit()

            # A token issued while an account is active must not be
            # consumable after that account has been deactivated.
            db.execute(text("""
                SELECT public.auth_issue_password_reset(
                    :user_id, :tenant_id, :token_hash, :expires_at
                )
            """), {
                "user_id": user_ids[tenant_b],
                "tenant_id": tenant_b,
                "token_hash": token_deactivated,
                "expires_at": now + timedelta(minutes=10),
            })
            db.commit()

            # Deactivated accounts cannot receive new reset credentials.
            with admin_engine.begin() as admin:
                admin.execute(
                    text("UPDATE users SET is_active = FALSE WHERE id = :user_id"),
                    {"user_id": user_ids[tenant_b]},
                )
            db.execute(text("""
                SELECT public.auth_issue_password_reset(
                    :user_id, :tenant_id, :token_hash, :expires_at
                )
            """), {
                "user_id": user_ids[tenant_b],
                "tenant_id": tenant_b,
                "token_hash": token_inactive,
                "expires_at": now + timedelta(minutes=10),
            })
            db.commit()

            deactivated_consumption = db.execute(text("""
                SELECT user_id, tenant_id
                FROM public.auth_consume_password_reset(:token_hash)
            """), {"token_hash": token_deactivated}).mappings().first()
            assert deactivated_consumption is None
            db.commit()

        with admin_engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT token_hash, user_id, tenant_id, used_at
                FROM password_reset_tokens
                WHERE token_hash IN (:valid, :mismatch, :expired, :inactive, :deactivated)
            """), {
                "valid": token_valid,
                "mismatch": token_mismatch,
                "expired": token_expired,
                "inactive": token_inactive,
                "deactivated": token_deactivated,
            }).all()
            assert {
                (row.token_hash, row.user_id, row.tenant_id)
                for row in rows
            } == {
                (token_valid, user_ids[tenant_a], tenant_a),
                (token_deactivated, user_ids[tenant_b], tenant_b),
            }
            by_hash = {row.token_hash: row for row in rows}
            assert by_hash[token_valid].used_at is not None
            # Deactivation blocks consumption but deliberately preserves the
            # token row for expiry/audit cleanup rather than deleting it.
            assert by_hash[token_deactivated].used_at is None
    finally:
        with admin_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM password_reset_tokens WHERE token_hash IN (:valid, :mismatch, :expired, :inactive, :deactivated)"),
                {"valid": token_valid, "mismatch": token_mismatch, "expired": token_expired, "inactive": token_inactive, "deactivated": token_deactivated},
            )
            conn.execute(
                text("DELETE FROM users WHERE tenant_id IN (:a, :b)"),
                {"a": tenant_a, "b": tenant_b},
            )
        admin_engine.dispose()


@pytest.mark.skipif(
    not os.getenv("RLS_TEST_ADMIN_DATABASE_URL"),
    reason="PostgreSQL default-privilege test requires RLS_TEST_ADMIN_DATABASE_URL",
)
def test_runtime_role_has_no_default_table_or_sequence_grants():
    """Future migration objects must not silently inherit runtime privileges."""
    admin_engine = create_engine(os.environ["RLS_TEST_ADMIN_DATABASE_URL"])
    try:
        with admin_engine.connect() as conn:
            defaults = conn.execute(text("""
                SELECT owner.rolname AS owner_name,
                       d.defaclobjtype AS object_type,
                       privilege.privilege_type
                FROM pg_default_acl AS d
                CROSS JOIN LATERAL aclexplode(d.defaclacl) AS privilege
                JOIN pg_roles AS owner ON owner.oid = d.defaclrole
                JOIN pg_roles AS grantee ON grantee.oid = privilege.grantee
                WHERE grantee.rolname = 'ledgergate_runtime'
                  AND d.defaclobjtype IN ('r', 'S')
            """)).all()
            assert defaults == []

            # Existing application tables are granted by migration 0016,
            # while authentication throttle/reset tables remain function-only.
            access = conn.execute(text("""
                SELECT
                  has_table_privilege('ledgergate_runtime', 'public.users', 'SELECT'),
                  has_table_privilege('ledgergate_runtime', 'public.invoices', 'INSERT'),
                  has_table_privilege('ledgergate_runtime', 'public.login_throttles', 'SELECT'),
                  has_table_privilege('ledgergate_runtime', 'public.password_reset_tokens', 'INSERT')
            """)).one()
            assert tuple(access) == (True, True, False, False)
    finally:
        admin_engine.dispose()

@pytest.mark.skipif(
    not os.getenv("RLS_TEST_ADMIN_DATABASE_URL"),
    reason="PostgreSQL migration regression requires RLS_TEST_ADMIN_DATABASE_URL",
)
def test_0017_revokes_legacy_default_grants_for_existing_owner_roles():
    """Migration 0017 removes pre-existing table and sequence default ACLs."""
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    admin_engine = create_engine(os.environ["RLS_TEST_ADMIN_DATABASE_URL"])
    owner_role = "ledgergate_legacy_acl_test_owner"
    migration = importlib.import_module(
        "migrations.versions.0017_revoke_legacy_runtime_defaults"
    )
    try:
        with admin_engine.begin() as conn:
            conn.execute(text(f'CREATE ROLE "{owner_role}" NOLOGIN'))
            conn.execute(text(
                f'ALTER DEFAULT PRIVILEGES FOR ROLE "{owner_role}" IN SCHEMA public '
                "GRANT ALL ON TABLES TO ledgergate_runtime"
            ))
            conn.execute(text(
                f'ALTER DEFAULT PRIVILEGES FOR ROLE "{owner_role}" IN SCHEMA public '
                "GRANT ALL ON SEQUENCES TO ledgergate_runtime"
            ))

            seeded = conn.execute(text("""
                SELECT count(*)
                FROM pg_default_acl AS defaults
                CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl
                JOIN pg_roles AS grantee ON grantee.oid = acl.grantee
                JOIN pg_roles AS owner ON owner.oid = defaults.defaclrole
                WHERE owner.rolname = :owner
                  AND grantee.rolname = 'ledgergate_runtime'
                  AND defaults.defaclobjtype IN ('r', 'S')
            """), {"owner": owner_role}).scalar_one()
            assert seeded > 0

            context = MigrationContext.configure(conn)
            with Operations.context(context):
                migration.upgrade()

            remaining = conn.execute(text("""
                SELECT count(*)
                FROM pg_default_acl AS defaults
                CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl
                JOIN pg_roles AS grantee ON grantee.oid = acl.grantee
                JOIN pg_roles AS owner ON owner.oid = defaults.defaclrole
                WHERE owner.rolname = :owner
                  AND grantee.rolname = 'ledgergate_runtime'
                  AND defaults.defaclobjtype IN ('r', 'S')
            """), {"owner": owner_role}).scalar_one()
            assert remaining == 0
            conn.execute(text(f'DROP ROLE "{owner_role}"'))
    finally:
        admin_engine.dispose()

