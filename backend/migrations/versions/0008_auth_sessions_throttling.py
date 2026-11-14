"""Add revocable authentication sessions and persistent login throttling."""
from alembic import op
import sqlalchemy as sa

revision = "0008_auth_sessions_throttling"
down_revision = "0007_approval_policy_versions"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "auth_sessions" not in tables:
        op.create_table(
            "auth_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("jti", sa.String(36), nullable=False, unique=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("tenant_id", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_auth_sessions_jti", "auth_sessions", ["jti"], unique=True)
        op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
        op.create_index("ix_auth_sessions_tenant_id", "auth_sessions", ["tenant_id"])
        op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    if "login_throttles" not in tables:
        op.create_table(
            "login_throttles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("email_key", sa.String(64), nullable=False, unique=True),
            sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_login_throttles_email_key", "login_throttles", ["email_key"], unique=True)
    if bind.dialect.name == "postgresql":
        op.execute('ALTER TABLE "auth_sessions" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "auth_sessions" FORCE ROW LEVEL SECURITY')
        op.execute('DROP POLICY IF EXISTS auth_session_tenant_isolation ON "auth_sessions"')
        op.execute("""
            CREATE POLICY auth_session_tenant_isolation ON "auth_sessions"
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
        """)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql" and "auth_sessions" in sa.inspect(bind).get_table_names():
        op.execute('DROP POLICY IF EXISTS auth_session_tenant_isolation ON "auth_sessions"')
        op.execute('ALTER TABLE "auth_sessions" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "auth_sessions" DISABLE ROW LEVEL SECURITY')
    op.drop_table("login_throttles")
    op.drop_table("auth_sessions")
