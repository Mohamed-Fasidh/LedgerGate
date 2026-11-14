"""Add one-time password-reset tokens.

The initial baseline currently creates tables from current SQLAlchemy metadata.
That means this table may already exist when this revision is reached. Keep
this revision safe for both that baseline and explicitly migrated databases.
"""
from alembic import op
import sqlalchemy as sa


revision = "0011_password_reset_tokens"
down_revision = "0010_refresh_token_rotation"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "password_reset_tokens" not in tables:
        op.create_table(
            "password_reset_tokens",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )

    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("password_reset_tokens")}
    expected_indexes = (
        ("ix_password_reset_tokens_user_id", ["user_id"]),
        ("ix_password_reset_tokens_tenant_id", ["tenant_id"]),
        ("ix_password_reset_tokens_expires_at", ["expires_at"]),
    )
    for name, columns in expected_indexes:
        if name not in indexes:
            op.create_index(name, "password_reset_tokens", columns)


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "password_reset_tokens" in set(inspector.get_table_names()):
        op.drop_table("password_reset_tokens")
