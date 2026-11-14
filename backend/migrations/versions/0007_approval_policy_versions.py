"""Add immutable tenant approval policy versions and invoice snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "0007_approval_policy_versions"
down_revision = "0006_invoice_dual_approval"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "approval_policies" not in tables:
        op.create_table(
            "approval_policies",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.String(64), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
            sa.Column("threshold_amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("required_approvals", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("tenant_id", "version", name="uq_approval_policy_tenant_version"),
        )
        op.create_index("ix_approval_policies_tenant_id", "approval_policies", ["tenant_id"])
        op.create_index("ix_approval_policy_tenant_version", "approval_policies", ["tenant_id", "version"])
    invoice_columns = {c["name"] for c in sa.inspect(bind).get_columns("invoices")}
    if "approval_policy_id" not in invoice_columns:
        op.add_column("invoices", sa.Column("approval_policy_id", sa.Integer(), nullable=True))
    if "approval_policy_version" not in invoice_columns:
        op.add_column("invoices", sa.Column("approval_policy_version", sa.Integer(), nullable=True))
    if "required_approvals_snapshot" not in invoice_columns:
        op.add_column("invoices", sa.Column("required_approvals_snapshot", sa.Integer(), nullable=True))
    if "approval_threshold_snapshot" not in invoice_columns:
        op.add_column("invoices", sa.Column("approval_threshold_snapshot", sa.Numeric(14, 2), nullable=True))
    if bind.dialect.name == "postgresql":
        invoice_fks = sa.inspect(bind).get_foreign_keys("invoices")
        has_policy_fk = any(fk.get("constrained_columns") == ["approval_policy_id"] and fk.get("referred_table") == "approval_policies" for fk in invoice_fks)
        if not has_policy_fk:
            op.create_foreign_key("fk_invoice_approval_policy", "invoices", "approval_policies", ["approval_policy_id"], ["id"])
        op.execute('ALTER TABLE "approval_policies" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "approval_policies" FORCE ROW LEVEL SECURITY')
        op.execute('DROP POLICY IF EXISTS approval_policy_tenant_isolation ON "approval_policies"')
        op.execute("""
            CREATE POLICY approval_policy_tenant_isolation ON "approval_policies"
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
        """)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute('DROP POLICY IF EXISTS approval_policy_tenant_isolation ON "approval_policies"')
        op.execute('ALTER TABLE "approval_policies" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "approval_policies" DISABLE ROW LEVEL SECURITY')
        invoice_fks = sa.inspect(bind).get_foreign_keys("invoices")
        policy_fk = next((fk.get("name") for fk in invoice_fks if fk.get("constrained_columns") == ["approval_policy_id"] and fk.get("referred_table") == "approval_policies"), None)
        if policy_fk:
            op.drop_constraint(policy_fk, "invoices", type_="foreignkey")
    op.drop_column("invoices", "approval_threshold_snapshot")
    op.drop_column("invoices", "required_approvals_snapshot")
    op.drop_column("invoices", "approval_policy_version")
    op.drop_column("invoices", "approval_policy_id")
    op.drop_table("approval_policies")
