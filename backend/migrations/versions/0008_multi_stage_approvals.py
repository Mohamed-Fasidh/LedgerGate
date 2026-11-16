"""Add tenant-scoped multi-stage invoice approval policies."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0008_multi_stage_approvals"
down_revision = "0007_upload_idempotency"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "approval_policies" not in tables:
        op.create_table(
            "approval_policies",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("min_amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("max_amount", sa.Numeric(14, 2), nullable=True),
            sa.Column("approval_steps", sa.JSON(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("tenant_id", "name", name="uq_tenant_approval_policy_name"),
        )
        op.create_index("ix_approval_policies_tenant_id", "approval_policies", ["tenant_id"])

    invoice_columns = {c["name"] for c in inspect(bind).get_columns("invoices")}
    if "approval_policy_id" not in invoice_columns:
        op.add_column("invoices", sa.Column("approval_policy_id", sa.Integer(), nullable=True))
        op.create_foreign_key("fk_invoices_approval_policy", "invoices", "approval_policies", ["approval_policy_id"], ["id"])
    if "approval_steps_snapshot" not in invoice_columns:
        op.add_column("invoices", sa.Column("approval_steps_snapshot", sa.JSON(), nullable=True))
    if "approval_stage" not in invoice_columns:
        op.add_column("invoices", sa.Column("approval_stage", sa.Integer(), nullable=False, server_default="1"))

    tables = set(inspect(bind).get_table_names())
    if "invoice_approvals" not in tables:
        op.create_table(
            "invoice_approvals",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.String(length=64), nullable=False),
            sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=False),
            sa.Column("policy_id", sa.Integer(), sa.ForeignKey("approval_policies.id"), nullable=True),
            sa.Column("stage", sa.Integer(), nullable=False),
            sa.Column("approver_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("decision", sa.String(length=20), nullable=False),
            sa.Column("comment", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("tenant_id", "invoice_id", "approver_user_id", name="uq_invoice_approval_actor"),
        )
        op.create_index("ix_invoice_approvals_tenant_id", "invoice_approvals", ["tenant_id"])
        op.create_index("ix_invoice_approvals_invoice_id", "invoice_approvals", ["invoice_id"])

    if bind.dialect.name == "postgresql":
        for table in ("approval_policies", "invoice_approvals"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"CREATE POLICY tenant_isolation ON {table} USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))")

def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in ("invoice_approvals", "approval_policies"):
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    tables = set(inspect(bind).get_table_names())
    if "invoice_approvals" in tables:
        op.drop_table("invoice_approvals")
    invoice_columns = {c["name"] for c in inspect(bind).get_columns("invoices")}
    if "approval_stage" in invoice_columns:
        op.drop_column("invoices", "approval_stage")
    if "approval_steps_snapshot" in invoice_columns:
        op.drop_column("invoices", "approval_steps_snapshot")
    if "approval_policy_id" in invoice_columns:
        op.drop_constraint("fk_invoices_approval_policy", "invoices", type_="foreignkey")
        op.drop_column("invoices", "approval_policy_id")
    if "approval_policies" in set(inspect(bind).get_table_names()):
        op.drop_table("approval_policies")
