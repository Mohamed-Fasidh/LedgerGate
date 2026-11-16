"""Persist operation and request fingerprints for invoice idempotency."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0007_upload_idempotency"
down_revision = "0006_login_throttling"
branch_labels = None
depends_on = None

def upgrade():
    columns = {c["name"] for c in inspect(op.get_bind()).get_columns("invoices")}
    if "idempotency_operation" not in columns:
        op.add_column("invoices", sa.Column("idempotency_operation", sa.String(length=32), nullable=True))
    if "idempotency_request_hash" not in columns:
        op.add_column("invoices", sa.Column("idempotency_request_hash", sa.String(length=64), nullable=True))

def downgrade():
    bind = op.get_bind()
    columns = {c["name"] for c in inspect(bind).get_columns("invoices")}
    if "idempotency_request_hash" in columns:
        op.drop_column("invoices", "idempotency_request_hash")
    if "idempotency_operation" in columns:
        op.drop_column("invoices", "idempotency_operation")
