"""Persist privacy-preserving login throttling counters."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0006_login_throttling"
down_revision = "0005_invoice_idempotency"
branch_labels = None
depends_on = None

def upgrade():
    if "login_attempts" not in inspect(op.get_bind()).get_table_names():
        op.create_table(
            "login_attempts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("email_hash", sa.String(length=64), nullable=False),
            sa.Column("ip_hash", sa.String(length=64), nullable=False),
            sa.Column("succeeded", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_login_attempts_email_hash", "login_attempts", ["email_hash"])
        op.create_index("ix_login_attempts_ip_hash", "login_attempts", ["ip_hash"])
        op.create_index("ix_login_attempts_attempted_at", "login_attempts", ["attempted_at"])
        op.create_index("ix_login_attempts_email_time", "login_attempts", ["email_hash", "attempted_at"])

def downgrade():
    if "login_attempts" in inspect(op.get_bind()).get_table_names():
        op.drop_table("login_attempts")
