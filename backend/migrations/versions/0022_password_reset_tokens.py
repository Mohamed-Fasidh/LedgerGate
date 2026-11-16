"""Persist hashed, expiring password reset tokens."""
from alembic import op
import sqlalchemy as sa

revision = "0022_password_reset_tokens"
down_revision = "0021_access_token_version"
branch_labels = None
depends_on = None


def upgrade():
    # The legacy 0001 migration creates current Base.metadata. On fresh installs
    # that can pre-create tables introduced by later revisions; keep this revision
    # safe for both fresh databases and databases upgraded from an older schema.
    inspector = sa.inspect(op.get_bind())
    if "password_reset_tokens" not in inspector.get_table_names():
        op.create_table(
            "password_reset_tokens",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("password_reset_tokens")}
    for name, columns in (
        ("ix_password_reset_tokens_user_id", ["user_id"]),
        ("ix_password_reset_tokens_expires_at", ["expires_at"]),
        ("ix_password_reset_tokens_user_created", ["user_id", "created_at"]),
    ):
        if name not in existing:
            op.create_index(name, "password_reset_tokens", columns)


def downgrade():
    if "password_reset_tokens" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("password_reset_tokens")
