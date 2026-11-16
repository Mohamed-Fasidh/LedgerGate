"""Add tenant-scoped invoice API idempotency keys."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0005_invoice_idempotency"
down_revision = "0004_invoice_controls"
branch_labels = None
depends_on = None

def upgrade():
    columns = {c["name"] for c in inspect(op.get_bind()).get_columns("invoices")}
    if "idempotency_key" not in columns:
        op.add_column("invoices", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
        op.create_unique_constraint("uq_invoice_tenant_idempotency", "invoices", ["tenant_id", "idempotency_key"])

def downgrade():
    bind = op.get_bind()
    columns = {c["name"] for c in inspect(bind).get_columns("invoices")}
    if "idempotency_key" in columns:
        op.drop_constraint("uq_invoice_tenant_idempotency", "invoices", type_="unique")
        op.drop_column("invoices", "idempotency_key")
