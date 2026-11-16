"""PostgreSQL integration tests for pre-authentication identity isolation."""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="Requires disposable PostgreSQL database",
)
def test_pre_auth_identity_tables_are_tenant_scoped_and_lookup_functions_are_narrow():
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    suffix = uuid.uuid4().hex
    tenant_a = "identity-a-" + suffix
    tenant_b = "identity-b-" + suffix
    email_a = f"identity-a-{suffix}@example.test"
    email_b = f"identity-b-{suffix}@example.test"
    verification_hash = "v" + suffix[:63]
    reset_hash = "r" + suffix[:63]
    now = datetime.now(timezone.utc)

    try:
        with engine.begin() as admin:
            user_a = admin.execute(text("""
                INSERT INTO public.users (
                    tenant_id, tenant_name, email, hashed_password, role,
                    is_active, email_verified, auth_token_version,
                    mfa_enabled, mfa_recovery_code_hashes, created_at
                )
                VALUES (
                    :tenant_id, 'Identity A', :email, 'test-hash', 'analyst',
                    TRUE, FALSE, 0, FALSE, '[]'::json, :created_at
                )
                RETURNING id
            """), {"tenant_id": tenant_a, "email": email_a, "created_at": now}).scalar_one()
            user_b = admin.execute(text("""
                INSERT INTO public.users (
                    tenant_id, tenant_name, email, hashed_password, role,
                    is_active, email_verified, auth_token_version,
                    mfa_enabled, mfa_recovery_code_hashes, created_at
                )
                VALUES (
                    :tenant_id, 'Identity B', :email, 'test-hash', 'analyst',
                    TRUE, TRUE, 0, FALSE, '[]'::json, :created_at
                )
                RETURNING id
            """), {"tenant_id": tenant_b, "email": email_b, "created_at": now}).scalar_one()

            admin.execute(text("""
                INSERT INTO public.email_verification_tokens (
                    user_id, tenant_id, token_hash, expires_at, used_at, created_at
                ) VALUES (:user_id, :tenant_id, :token_hash, :expires_at, NULL, :created_at)
            """), {
                "user_id": user_a, "tenant_id": tenant_a,
                "token_hash": verification_hash, "expires_at": now + timedelta(hours=1),
                "created_at": now,
            })
            admin.execute(text("""
                INSERT INTO public.password_reset_tokens (
                    user_id, tenant_id, token_hash, expires_at, used_at, created_at
                ) VALUES (:user_id, :tenant_id, :token_hash, :expires_at, NULL, :created_at)
            """), {
                "user_id": user_b, "tenant_id": tenant_b,
                "token_hash": reset_hash, "expires_at": now + timedelta(minutes=15),
                "created_at": now,
            })

        # No tenant context exposes no user or token rows to the runtime role.
        with engine.begin() as runtime:
            runtime.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            runtime.execute(text("RESET app.tenant_id"))
            assert runtime.execute(text("""
                SELECT count(*) FROM public.users
                WHERE email IN (:email_a, :email_b)
            """), {"email_a": email_a, "email_b": email_b}).scalar_one() == 0
            assert runtime.execute(text("""
                SELECT count(*) FROM public.email_verification_tokens
                WHERE token_hash = :token_hash
            """), {"token_hash": verification_hash}).scalar_one() == 0
            assert runtime.execute(text("""
                SELECT count(*) FROM public.password_reset_tokens
                WHERE token_hash = :token_hash
            """), {"token_hash": reset_hash}).scalar_one() == 0

            # The only pre-authentication disclosure is a minimal owner lookup.
            login_owner = runtime.execute(text("""
                SELECT user_id, tenant_id
                FROM public.auth_lookup_user(:email)
            """), {"email": email_b}).mappings().one()
            assert login_owner["user_id"] == user_b
            assert login_owner["tenant_id"] == tenant_b
            assert runtime.execute(
                text("SELECT public.auth_email_exists(:email)"),
                {"email": email_b},
            ).scalar_one() is True

            reset_owner = runtime.execute(text("""
                SELECT user_id, tenant_id
                FROM public.auth_lookup_password_reset(:token_hash)
            """), {"token_hash": reset_hash}).mappings().one()
            assert reset_owner["user_id"] == user_b
            assert reset_owner["tenant_id"] == tenant_b

            verification_owner = runtime.execute(text("""
                SELECT user_id, tenant_id
                FROM public.auth_lookup_email_verification(:token_hash)
            """), {"token_hash": verification_hash}).mappings().one()
            assert verification_owner["user_id"] == user_a
            assert verification_owner["tenant_id"] == tenant_a

        # Once the lookup result establishes tenant context, ordinary ORM/SQL
        # reads are constrained to that tenant by FORCE RLS.
        with engine.begin() as runtime:
            runtime.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            runtime.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": tenant_a})
            visible_users = runtime.execute(text("""
                SELECT email FROM public.users
                WHERE email IN (:email_a, :email_b)
                ORDER BY email
            """), {"email_a": email_a, "email_b": email_b}).scalars().all()
            assert visible_users == [email_a]
            visible_tokens = runtime.execute(text("""
                SELECT token_hash FROM public.email_verification_tokens
                WHERE token_hash IN (:verification_hash, :reset_hash)
            """), {"verification_hash": verification_hash, "reset_hash": reset_hash}).scalars().all()
            assert visible_tokens == [verification_hash]
            with pytest.raises(Exception, match="row-level security"):
                runtime.execute(text("""
                    INSERT INTO public.password_reset_tokens (
                        user_id, tenant_id, token_hash, expires_at, used_at, created_at
                    ) VALUES (:user_id, :tenant_b, :token_hash, :expires_at, NULL, :created_at)
                """), {
                    "user_id": user_b, "tenant_b": tenant_b,
                    "token_hash": "x" + suffix[:63],
                    "expires_at": now + timedelta(minutes=10), "created_at": now,
                })

    finally:
        with engine.begin() as admin:
            admin.execute(text("""
                DELETE FROM public.email_verification_tokens
                WHERE token_hash = :token_hash
            """), {"token_hash": verification_hash})
            admin.execute(text("""
                DELETE FROM public.password_reset_tokens
                WHERE token_hash = :token_hash
            """), {"token_hash": reset_hash})
            admin.execute(text("""
                DELETE FROM public.users WHERE tenant_id IN (:tenant_a, :tenant_b)
            """), {"tenant_a": tenant_a, "tenant_b": tenant_b})
        engine.dispose()
