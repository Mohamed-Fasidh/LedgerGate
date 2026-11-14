"""Persist independent reviewer decisions for invoices."""
from alembic import op
import sqlalchemy as sa

revision = "0006_invoice_dual_approval"
down_revision = "0005_idempotency_records"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "invoice_approvals" not in inspector.get_table_names():
        op.create_table(
            "invoice_approvals",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.String(64), nullable=False),
            sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=False),
            sa.Column("reviewer_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("tenant_id", "invoice_id", "reviewer_user_id", name="uq_invoice_approval_reviewer"),
        )
        op.create_index("ix_invoice_approvals_tenant_id", "invoice_approvals", ["tenant_id"])
        op.create_index("ix_invoice_approvals_invoice_id", "invoice_approvals", ["invoice_id"])
        op.create_index("ix_invoice_approval_invoice", "invoice_approvals", ["tenant_id", "invoice_id"])

    if bind.dialect.name == "postgresql":
        op.execute('ALTER TABLE "invoice_approvals" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "invoice_approvals" FORCE ROW LEVEL SECURITY')
        op.execute('DROP POLICY IF EXISTS invoice_approval_tenant_isolation ON "invoice_approvals"')
        op.execute("""
            CREATE POLICY invoice_approval_tenant_isolation ON "invoice_approvals"
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
        """)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute('DROP POLICY IF EXISTS invoice_approval_tenant_isolation ON "invoice_approvals"')
        op.execute('ALTER TABLE "invoice_approvals" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "invoice_approvals" DISABLE ROW LEVEL SECURITY')
    op.drop_table("invoice_approvals")
