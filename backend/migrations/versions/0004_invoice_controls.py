"""Add maker-checker attribution and optimistic invoice concurrency."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0004_invoice_controls"
down_revision = "0003_security_rls"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    columns = {c["name"] for c in inspect(bind).get_columns("invoices")}
    if "created_by_user_id" not in columns:
        op.add_column("invoices", sa.Column("created_by_user_id", sa.Integer(), nullable=True))
        op.create_foreign_key("fk_invoices_created_by_user", "invoices", "users", ["created_by_user_id"], ["id"])
    if "version" not in columns:
        op.add_column("invoices", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))

def downgrade():
    bind = op.get_bind()
    columns = {c["name"] for c in inspect(bind).get_columns("invoices")}
    if "created_by_user_id" in columns:
        op.drop_constraint("fk_invoices_created_by_user", "invoices", type_="foreignkey")
        op.drop_column("invoices", "created_by_user_id")
    if "version" in columns:
        op.drop_column("invoices", "version")
