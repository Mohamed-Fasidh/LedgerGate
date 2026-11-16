"""Create encrypted durable outbox for account-security email."""
from alembic import op
import sqlalchemy as sa

revision = "0024_email_outbox"
down_revision = "0023_email_verification"
branch_labels = None
depends_on = None


def upgrade():
    if "outbound_emails" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "outbound_emails",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("recipient", sa.String(length=255), nullable=False),
            sa.Column("message_type", sa.String(length=40), nullable=False),
            sa.Column("encrypted_payload", sa.Text(), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="12"),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.String(length=120), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("outbound_emails")}
    for name, columns in (
        ("ix_outbound_emails_pending_due", ["status", "next_attempt_at", "id"]),
        ("ix_outbound_emails_token_hash", ["token_hash"]),
        ("ix_outbound_emails_locked_at", ["status", "locked_at"]),
    ):
        if name not in existing:
            op.create_index(name, "outbound_emails", columns)


def downgrade():
    if "outbound_emails" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("outbound_emails")
