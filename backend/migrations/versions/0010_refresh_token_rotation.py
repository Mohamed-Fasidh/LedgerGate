"""Add one-time refresh token hashes to authentication sessions."""
from alembic import op
import sqlalchemy as sa

revision = "0010_refresh_token_rotation"
down_revision = "0009_user_totp_mfa"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("auth_sessions")}
    if "refresh_token_hash" not in columns:
        op.add_column("auth_sessions", sa.Column("refresh_token_hash", sa.String(length=64), nullable=True))
        op.create_index("ix_auth_sessions_refresh_token_hash", "auth_sessions", ["refresh_token_hash"], unique=True)
    if "refresh_expires_at" not in columns:
        op.add_column("auth_sessions", sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("auth_sessions")}
    if "refresh_expires_at" in columns:
        op.drop_column("auth_sessions", "refresh_expires_at")
    if "refresh_token_hash" in columns:
        op.drop_index("ix_auth_sessions_refresh_token_hash", table_name="auth_sessions")
        op.drop_column("auth_sessions", "refresh_token_hash")
