"""Add account email verification state and one-time verification tokens."""
from alembic import op
import sqlalchemy as sa

revision = "0023_email_verification"
down_revision = "0022_password_reset_tokens"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "email_verified" not in user_columns:
        op.add_column(
            "users",
            sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.true()),
        )

    inspector = sa.inspect(op.get_bind())
    if "email_verification_tokens" not in inspector.get_table_names():
        op.create_table(
            "email_verification_tokens",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("email_verification_tokens")}
    for name, columns in (
        ("ix_email_verification_tokens_user_id", ["user_id"]),
        ("ix_email_verification_tokens_expires_at", ["expires_at"]),
        ("ix_email_verification_tokens_user_created", ["user_id", "created_at"]),
    ):
        if name not in existing:
            op.create_index(name, "email_verification_tokens", columns)


def downgrade():
    if "email_verification_tokens" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("email_verification_tokens")
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "email_verified" in columns:
        op.drop_column("users", "email_verified")
