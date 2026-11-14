"""Add encrypted TOTP MFA state to users."""
from alembic import op
import sqlalchemy as sa

revision = "0009_user_totp_mfa"
down_revision = "0008_auth_sessions_throttling"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "mfa_enabled" not in columns:
        op.add_column("users", sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "mfa_secret_encrypted" not in columns:
        op.add_column("users", sa.Column("mfa_secret_encrypted", sa.String(length=512), nullable=True))


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "mfa_secret_encrypted" in columns:
        op.drop_column("users", "mfa_secret_encrypted")
    if "mfa_enabled" in columns:
        op.drop_column("users", "mfa_enabled")
