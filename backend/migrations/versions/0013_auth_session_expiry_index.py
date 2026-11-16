"""Index tenant-scoped refresh-session expiry cleanup."""
from alembic import op
import sqlalchemy as sa

revision = "0013_auth_session_expiry_index"
down_revision = "0012_unified_invoice_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("auth_sessions")}
    if "ix_auth_sessions_tenant_expires" not in indexes:
        op.create_index(
            "ix_auth_sessions_tenant_expires",
            "auth_sessions",
            ["tenant_id", "expires_at"],
        )


def downgrade():
    bind = op.get_bind()
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("auth_sessions")}
    if "ix_auth_sessions_tenant_expires" in indexes:
        op.drop_index("ix_auth_sessions_tenant_expires", table_name="auth_sessions")
