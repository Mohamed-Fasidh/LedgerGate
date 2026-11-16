"""Index active session listing and family revocation queries."""
from alembic import op
import sqlalchemy as sa

revision = "0020_auth_session_query_indexes"
down_revision = "0019_idempotency_cleanup_index"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_auth_sessions_tenant_user_active_created",
     ["tenant_id", "user_id", "revoked_at", "created_at", "id"]),
    ("ix_auth_sessions_tenant_family_active",
     ["tenant_id", "family_id", "revoked_at"]),
)


def upgrade():
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("auth_sessions")}
    for name, columns in INDEXES:
        if name not in existing:
            op.create_index(name, "auth_sessions", columns)


def downgrade():
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("auth_sessions")}
    for name, _columns in reversed(INDEXES):
        if name in existing:
            op.drop_index(name, table_name="auth_sessions")
