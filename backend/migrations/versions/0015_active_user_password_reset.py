"""Require active accounts when consuming password-reset credentials."""
from alembic import op

revision = "0015_active_reset"
down_revision = "0014_auth_auth_db"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # Preserve the dedicated SECURITY DEFINER owner when replacing the function.
    op.execute("""
        CREATE OR REPLACE FUNCTION public.auth_consume_password_reset(p_token_hash varchar)
        RETURNS TABLE(user_id integer, tenant_id varchar)
        LANGUAGE sql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
          UPDATE public.password_reset_tokens AS t SET used_at = now()
          WHERE t.token_hash = p_token_hash
            AND t.used_at IS NULL
            AND t.expires_at > now()
            AND EXISTS (
              SELECT 1
              FROM public.users AS u
              WHERE u.id = t.user_id
                AND u.tenant_id = t.tenant_id
                AND u.is_active = TRUE
            )
          RETURNING t.user_id, t.tenant_id
        $fn$
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION public.auth_consume_password_reset(character varying) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.auth_consume_password_reset(character varying) TO ledgergate_runtime"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute("""
        CREATE OR REPLACE FUNCTION public.auth_consume_password_reset(p_token_hash varchar)
        RETURNS TABLE(user_id integer, tenant_id varchar)
        LANGUAGE sql SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
          UPDATE public.password_reset_tokens AS t SET used_at = now()
          WHERE t.token_hash = p_token_hash
            AND t.used_at IS NULL
            AND t.expires_at > now()
          RETURNING t.user_id, t.tenant_id
        $fn$
    """)
    op.execute(
        "REVOKE ALL ON FUNCTION public.auth_consume_password_reset(character varying) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.auth_consume_password_reset(character varying) TO ledgergate_runtime"
    )
