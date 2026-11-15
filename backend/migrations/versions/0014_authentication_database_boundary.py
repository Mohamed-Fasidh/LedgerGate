"""Restrict pre-authentication data access to narrowly scoped database functions."""
from alembic import op

revision = "0014_authentication_database_boundary"
down_revision = "0013_tenant_rls_extended"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # Provisioned by Docker bootstrap and CI. The migration principal must be
    # a member so SET ROLE can assign ownership to the dedicated function role.
    op.execute("GRANT SELECT ON users TO ledgergate_auth_owner")
    op.execute("GRANT SELECT, INSERT, UPDATE ON login_throttles TO ledgergate_auth_owner")
    op.execute("GRANT SELECT, INSERT, UPDATE ON password_reset_tokens TO ledgergate_auth_owner")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE login_throttles_id_seq TO ledgergate_auth_owner")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE password_reset_tokens_id_seq TO ledgergate_auth_owner")

    op.execute('ALTER TABLE "users" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "users" FORCE ROW LEVEL SECURITY')
    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "users"')
    op.execute("""
        CREATE POLICY tenant_isolation ON users
        USING (tenant_id = current_setting('app.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
    """)

    op.execute("SET LOCAL ROLE ledgergate_auth_owner")
    op.execute("""
        CREATE FUNCTION public.auth_lookup_user(p_email text)
        RETURNS TABLE(user_id integer, tenant_id varchar)
        LANGUAGE sql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
          SELECT u.id, u.tenant_id FROM public.users AS u
          WHERE u.email = lower(btrim(p_email)) AND u.is_active
          LIMIT 1
        $fn$
    """)
    op.execute("""
        CREATE FUNCTION public.auth_email_exists(p_email text)
        RETURNS boolean
        LANGUAGE sql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
          SELECT EXISTS (SELECT 1 FROM public.users AS u WHERE u.email = lower(btrim(p_email)))
        $fn$
    """)
    op.execute("""
        CREATE FUNCTION public.auth_login_is_locked(p_key varchar)
        RETURNS boolean
        LANGUAGE sql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
          SELECT COALESCE((SELECT locked_until > now() FROM public.login_throttles WHERE email_key = p_key), false)
        $fn$
    """)
    op.execute("""
        CREATE FUNCTION public.auth_record_login_failure(p_key varchar)
        RETURNS void
        LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
        DECLARE v_now timestamptz := now();
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtext(p_key));
          INSERT INTO public.login_throttles (email_key, failed_attempts, window_started_at, updated_at)
          VALUES (p_key, 0, v_now, v_now) ON CONFLICT (email_key) DO NOTHING;
          UPDATE public.login_throttles
          SET failed_attempts = CASE WHEN v_now - window_started_at >= interval '15 minutes' THEN 1 ELSE failed_attempts + 1 END,
              window_started_at = CASE WHEN v_now - window_started_at >= interval '15 minutes' THEN v_now ELSE window_started_at END,
              locked_until = CASE WHEN (CASE WHEN v_now - window_started_at >= interval '15 minutes' THEN 1 ELSE failed_attempts + 1 END) >= 5 THEN v_now + interval '15 minutes' ELSE NULL END,
              updated_at = v_now
          WHERE email_key = p_key;
        END
        $fn$
    """)
    op.execute("""
        CREATE FUNCTION public.auth_clear_login_failures(p_key varchar)
        RETURNS void LANGUAGE sql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
          UPDATE public.login_throttles SET failed_attempts = 0, window_started_at = now(), locked_until = NULL, updated_at = now()
          WHERE email_key = p_key
        $fn$
    """)
    op.execute("""
        CREATE FUNCTION public.auth_issue_password_reset(p_user_id integer, p_tenant_id varchar, p_token_hash varchar, p_expires_at timestamptz)
        RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
        BEGIN
          UPDATE public.password_reset_tokens SET used_at = now() WHERE user_id = p_user_id AND used_at IS NULL;
          INSERT INTO public.password_reset_tokens (user_id, tenant_id, token_hash, expires_at, created_at)
          VALUES (p_user_id, p_tenant_id, p_token_hash, p_expires_at, now());
        END
        $fn$
    """)
    op.execute("""
        CREATE FUNCTION public.auth_consume_password_reset(p_token_hash varchar)
        RETURNS TABLE(user_id integer, tenant_id varchar)
        LANGUAGE sql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
          UPDATE public.password_reset_tokens AS t SET used_at = now()
          WHERE t.token_hash = p_token_hash AND t.used_at IS NULL AND t.expires_at > now()
          RETURNING t.user_id, t.tenant_id
        $fn$
    """)
    op.execute("RESET ROLE")

    op.execute("REVOKE ALL ON login_throttles, password_reset_tokens FROM ledgergate_runtime")
    functions = [
        ("auth_lookup_user", "text"),
        ("auth_email_exists", "text"),
        ("auth_login_is_locked", "character varying"),
        ("auth_record_login_failure", "character varying"),
        ("auth_clear_login_failures", "character varying"),
        ("auth_issue_password_reset", "integer, character varying, character varying, timestamp with time zone"),
        ("auth_consume_password_reset", "character varying"),
    ]
    for name, signature in functions:
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{name}({signature}) TO ledgergate_runtime")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON login_throttles, password_reset_tokens TO ledgergate_runtime")
    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "users"')
    op.execute('ALTER TABLE "users" NO FORCE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "users" DISABLE ROW LEVEL SECURITY')
    functions = [
        ("auth_consume_password_reset", "character varying"),
        ("auth_issue_password_reset", "integer, character varying, character varying, timestamp with time zone"),
        ("auth_clear_login_failures", "character varying"),
        ("auth_record_login_failure", "character varying"),
        ("auth_login_is_locked", "character varying"),
        ("auth_email_exists", "text"),
        ("auth_lookup_user", "text"),
    ]
    for name, signature in functions:
        op.execute(f"DROP FUNCTION IF EXISTS public.{name}({signature})")
