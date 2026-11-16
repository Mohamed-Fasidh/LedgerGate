"""Store one-time MFA recovery-code hashes."""
from alembic import op
import sqlalchemy as sa

revision = "0015_mfa_recovery_codes"
down_revision = "0014_user_totp_mfa"
branch_labels = None
depends_on = None


def upgrade():
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("users")}
    if "mfa_recovery_code_hashes" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "mfa_recovery_code_hashes",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )


def downgrade():
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("users")}
    if "mfa_recovery_code_hashes" in columns:
        op.drop_column("users", "mfa_recovery_code_hashes")
