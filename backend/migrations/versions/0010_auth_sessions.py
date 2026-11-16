
"""Persist refresh-token sessions and rotation state."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0010_auth_sessions"
down_revision = "0009_audit_hash_chain"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    if "auth_sessions" not in inspect(bind).get_table_names():
        op.create_table(
            "auth_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.String(64), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("token_id", sa.String(36), nullable=False, unique=True),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("family_id", sa.String(36), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("replaced_by_token_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_auth_sessions_tenant_id", "auth_sessions", ["tenant_id"])
        op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
        op.create_index("ix_auth_sessions_family_id", "auth_sessions", ["family_id"])
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE auth_sessions ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE auth_sessions FORCE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY tenant_isolation ON auth_sessions USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))")

def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON auth_sessions")
        op.execute("ALTER TABLE auth_sessions NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE auth_sessions DISABLE ROW LEVEL SECURITY")
    if "auth_sessions" in inspect(bind).get_table_names():
        op.drop_table("auth_sessions")
