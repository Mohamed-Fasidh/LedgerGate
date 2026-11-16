"""Add encrypted TOTP multi-factor authentication state to users."""
from alembic import op
import sqlalchemy as sa

revision = "0014_user_totp_mfa"
down_revision = "0013_auth_session_expiry_index"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("users")}
    if "mfa_secret_encrypted" not in columns:
        op.add_column("users", sa.Column("mfa_secret_encrypted", sa.String(512), nullable=True))
    if "mfa_enabled" not in columns:
        op.add_column("users", sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "mfa_enabled_at" not in columns:
        op.add_column("users", sa.Column("mfa_enabled_at", sa.DateTime(timezone=True), nullable=True))
    if "mfa_last_used_step" not in columns:
        op.add_column("users", sa.Column("mfa_last_used_step", sa.Integer(), nullable=True))
    if "mfa_enabled" in columns:
        op.execute("UPDATE users SET mfa_enabled = FALSE WHERE mfa_enabled IS NULL")


def downgrade():
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("users")}
    for name in ("mfa_last_used_step", "mfa_enabled_at", "mfa_enabled", "mfa_secret_encrypted"):
        if name in columns:
            op.drop_column("users", name)
