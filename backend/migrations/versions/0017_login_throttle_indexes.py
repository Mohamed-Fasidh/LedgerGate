"""Index login failure windows for bounded authentication throttling."""
from alembic import op
import sqlalchemy as sa

revision = "0017_login_throttle_indexes"
down_revision = "0016_registration_throttling"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    existing = set(inspector.get_indexes("login_attempts"))
    if "ix_login_attempts_email_failed_time" not in existing:
        op.create_index(
            "ix_login_attempts_email_failed_time",
            "login_attempts",
            ["email_hash", "succeeded", "attempted_at"],
        )
    if "ix_login_attempts_ip_failed_time" not in existing:
        op.create_index(
            "ix_login_attempts_ip_failed_time",
            "login_attempts",
            ["ip_hash", "succeeded", "attempted_at"],
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    existing = {item["name"] for item in inspector.get_indexes("login_attempts")}
    if "ix_login_attempts_ip_failed_time" in existing:
        op.drop_index("ix_login_attempts_ip_failed_time", table_name="login_attempts")
    if "ix_login_attempts_email_failed_time" in existing:
        op.drop_index("ix_login_attempts_email_failed_time", table_name="login_attempts")
