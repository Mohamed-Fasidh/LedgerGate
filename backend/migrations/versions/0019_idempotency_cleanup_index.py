"""Index tenant-scoped idempotency retention cleanup."""
from alembic import op
import sqlalchemy as sa

revision = "0019_idempotency_cleanup_index"
down_revision = "0018_tenant_pagination_indexes"
branch_labels = None
depends_on = None


def upgrade():
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("idempotency_records")}
    if "ix_idempotency_tenant_created_id" not in existing:
        op.create_index(
            "ix_idempotency_tenant_created_id",
            "idempotency_records",
            ["tenant_id", "created_at", "id"],
        )


def downgrade():
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("idempotency_records")}
    if "ix_idempotency_tenant_created_id" in existing:
        op.drop_index("ix_idempotency_tenant_created_id", table_name="idempotency_records")
