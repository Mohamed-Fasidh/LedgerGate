"""Apply tenant RLS to identity and one-time authentication records.

A non-login BYPASSRLS owner holds narrowly scoped SECURITY DEFINER lookup
functions. The application role can discover only a matching user/token's
tenant and must then access rows through the ordinary tenant RLS policy.
"""
from alembic import op
import sqlalchemy as sa

revision = "0026_preauth_identity_rls"
down_revision = "0025_runtime_privilege_defaults"
branch_labels = None
depends_on = None


def _add_tenant_column(table: str) -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns(table)}
    if "tenant_id" not in columns:
        op.add_column(table, sa.Column("tenant_id", sa.String(length=64), nullable=True))
    op.execute(
        f"""
        UPDATE public.{table} AS token
        SET tenant_id = account.tenant_id
        FROM public.users AS account
        WHERE token.user_id = account.id
          AND token.tenant_id IS NULL
        """
    )
    op.alter_column(table, "tenant_id", existing_type=sa.String(length=64), nullable=False)
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
    index_name = f"ix_{table}_tenant_user"
    if index_name not in indexes:
        op.create_index(index_name, table, ["tenant_id", "user_id"])


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Existing one-time tokens inherit the owning user's tenant before RLS is
    # enabled. Foreign keys ensure no token can reference a missing user.
    _add_tenant_column("password_reset_tokens")
    _add_tenant_column("email_verification_tokens")

    for table in ("users", "password_reset_tokens", "email_verification_tokens"):
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public."{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON public."{table}"')
        op.execute(f"""
            CREATE POLICY tenant_isolation ON public."{table}"
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))
        """)

    op.execute("""
        GRANT SELECT ON public.users,
                        public.password_reset_tokens,
                        public.email_verification_tokens
        TO ledgergate_auth_owner
    """)

    # Create functions as the dedicated non-login role. It has BYPASSRLS but
    # no application connection can assume it; only these fixed functions are
    # executable by the runtime role.
    # Temporarily allow the function owner to create only these reviewed functions.
    # The grant is revoked immediately after creation so BYPASSRLS does not imply
    # a lasting ability to add arbitrary objects to the application schema.
    op.execute("GRANT CREATE ON SCHEMA public TO ledgergate_auth_owner")
    op.execute("SET LOCAL ROLE ledgergate_auth_owner")
    op.execute("""
        CREATE OR REPLACE FUNCTION public.auth_lookup_user(p_email text)
        RETURNS TABLE(user_id integer, tenant_id varchar, is_active boolean, email_verified boolean)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
            SELECT u.id, u.tenant_id, u.is_active, u.email_verified
            FROM public.users AS u
            WHERE lower(btrim(u.email)) = lower(btrim(p_email))
            LIMIT 1
        $fn$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.auth_email_exists(p_email text)
        RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
            SELECT EXISTS (
                SELECT 1 FROM public.users AS u
                WHERE lower(btrim(u.email)) = lower(btrim(p_email))
            )
        $fn$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.auth_lookup_password_reset(p_token_hash varchar)
        RETURNS TABLE(user_id integer, tenant_id varchar)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
            SELECT token.user_id, token.tenant_id
            FROM public.password_reset_tokens AS token
            JOIN public.users AS account
              ON account.id = token.user_id
             AND account.tenant_id = token.tenant_id
            WHERE token.token_hash = p_token_hash
              AND account.is_active = TRUE
              AND account.email_verified = TRUE
            LIMIT 1
        $fn$
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.auth_lookup_email_verification(p_token_hash varchar)
        RETURNS TABLE(user_id integer, tenant_id varchar)
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $fn$
            SELECT token.user_id, token.tenant_id
            FROM public.email_verification_tokens AS token
            JOIN public.users AS account
              ON account.id = token.user_id
             AND account.tenant_id = token.tenant_id
            WHERE token.token_hash = p_token_hash
              AND account.is_active = TRUE
              AND account.email_verified = FALSE
            LIMIT 1
        $fn$
    """)
    op.execute("RESET ROLE")
    op.execute("REVOKE CREATE ON SCHEMA public FROM ledgergate_auth_owner")

    functions = (
        ("auth_lookup_user", "text"),
        ("auth_email_exists", "text"),
        ("auth_lookup_password_reset", "character varying"),
        ("auth_lookup_email_verification", "character varying"),
    )
    for name, signature in functions:
        # The runtime role is intentionally provisioned after all migrations.
        # PUBLIC execution is revoked here; the runtime bootstrap grants the
        # narrow EXECUTE allowlist once that role exists.
        op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    functions = (
        ("auth_lookup_email_verification", "character varying"),
        ("auth_lookup_password_reset", "character varying"),
        ("auth_email_exists", "text"),
        ("auth_lookup_user", "text"),
    )
    # The functions are owned by the dedicated NOLOGIN role. The migration
    # principal is a member of that role and must assume it to drop them.
    op.execute("SET LOCAL ROLE ledgergate_auth_owner")
    for name, signature in functions:
        op.execute(f"DROP FUNCTION IF EXISTS public.{name}({signature})")
    op.execute("RESET ROLE")
    op.execute("""
        REVOKE SELECT ON public.users,
                        public.password_reset_tokens,
                        public.email_verification_tokens
        FROM ledgergate_auth_owner
    """)
    for table in ("email_verification_tokens", "password_reset_tokens", "users"):
        op.execute(f'DROP POLICY IF EXISTS tenant_isolation ON public."{table}"')
        op.execute(f'ALTER TABLE public."{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public."{table}" DISABLE ROW LEVEL SECURITY')
    for table in ("email_verification_tokens", "password_reset_tokens"):
        indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}
        index_name = f"ix_{table}_tenant_user"
        if index_name in indexes:
            op.drop_index(index_name, table_name=table)
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
        if "tenant_id" in columns:
            op.drop_column(table, "tenant_id")
