"""Composite indexes for tenant-scoped paginated collections."""
from alembic import op
import sqlalchemy as sa

revision = "0018_tenant_pagination_indexes"
down_revision = "0017_login_throttle_indexes"
branch_labels = None
depends_on = None

INDEXES = (
    ("users", "ix_users_tenant_created_id", ["tenant_id", "created_at", "id"]),
    ("suppliers", "ix_suppliers_tenant_id_id", ["tenant_id", "id"]),
    ("purchase_orders", "ix_purchase_orders_tenant_id_id", ["tenant_id", "id"]),
    ("invoices", "ix_invoices_tenant_created_id", ["tenant_id", "created_at", "id"]),
    ("invoices", "ix_invoices_tenant_status_created_id", ["tenant_id", "status", "created_at", "id"]),
    ("audit_events", "ix_audit_events_tenant_created_id", ["tenant_id", "created_at", "id"]),
    ("audit_events", "ix_audit_events_tenant_id_id", ["tenant_id", "id"]),
    ("approval_policies", "ix_approval_policies_tenant_min_id", ["tenant_id", "min_amount", "id"]),
)


def upgrade():
    bind = op.get_bind()
    for table_name, index_name, columns in INDEXES:
        existing = {item["name"] for item in sa.inspect(bind).get_indexes(table_name)}
        if index_name not in existing:
            op.create_index(index_name, table_name, columns)


def downgrade():
    bind = op.get_bind()
    for table_name, index_name, _columns in reversed(INDEXES):
        existing = {item["name"] for item in sa.inspect(bind).get_indexes(table_name)}
        if index_name in existing:
            op.drop_index(index_name, table_name=table_name)
