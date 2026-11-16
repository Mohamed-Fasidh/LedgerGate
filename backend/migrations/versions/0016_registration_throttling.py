"""Add persistent privacy-preserving registration throttling."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0016_registration_throttling"
down_revision = "0015_mfa_recovery_codes"
branch_labels = None
depends_on = None


def upgrade():
    if "registration_attempts" not in inspect(op.get_bind()).get_table_names():
        op.create_table(
            "registration_attempts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("email_hash", sa.String(length=64), nullable=False),
            sa.Column("ip_hash", sa.String(length=64), nullable=False),
            sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_registration_attempts_email_hash", "registration_attempts", ["email_hash"])
        op.create_index("ix_registration_attempts_ip_hash", "registration_attempts", ["ip_hash"])
        op.create_index("ix_registration_attempts_attempted_at", "registration_attempts", ["attempted_at"])
        op.create_index("ix_registration_attempts_email_time", "registration_attempts", ["email_hash", "attempted_at"])
        op.create_index("ix_registration_attempts_ip_time", "registration_attempts", ["ip_hash", "attempted_at"])


def downgrade():
    if "registration_attempts" in inspect(op.get_bind()).get_table_names():
        op.drop_table("registration_attempts")
