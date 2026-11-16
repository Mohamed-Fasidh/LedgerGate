"""Add per-account access-token revocation version."""
from alembic import op
import sqlalchemy as sa

revision = "0021_access_token_version"
down_revision = "0020_auth_session_query_indexes"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "auth_token_version" not in columns:
        op.add_column(
            "users",
            sa.Column("auth_token_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "auth_token_version" in columns:
        op.drop_column("users", "auth_token_version")
