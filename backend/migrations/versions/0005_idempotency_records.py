"""Persist idempotency keys for invoice decision requests."""
from alembic import op
import sqlalchemy as sa

revision = "0005_idempotency_records"
down_revision = "0004_invoice_maker_checker"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "idempotency_records" not in inspector.get_table_names():
        op.create_table(
            "idempotency_records",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.String(64), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("key", sa.String(128), nullable=False),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column("response_status", sa.Integer(), nullable=True),
            sa.Column("response_body", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("tenant_id", "user_id", "key", name="uq_idempotency_tenant_user_key"),
        )
        op.create_index("ix_idempotency_records_tenant_id", "idempotency_records", ["tenant_id"])
        op.create_index("ix_idempotency_expires_at", "idempotency_records", ["expires_at"])

    if bind.dialect.name == "postgresql":
        op.execute('ALTER TABLE "idempotency_records" ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "idempotency_records" FORCE ROW LEVEL SECURITY')
        # Keep policy DDL outside a PL/pgSQL DO block: CREATE POLICY is utility
        # DDL and should be executed as its own migration statement.
        op.execute('DROP POLICY IF EXISTS tenant_isolation ON "idempotency_records"')
        op.execute("""
            CREATE POLICY tenant_isolation ON "idempotency_records"
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
        """)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute('DROP POLICY IF EXISTS tenant_isolation ON "idempotency_records"')
        op.execute('ALTER TABLE "idempotency_records" NO FORCE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE "idempotency_records" DISABLE ROW LEVEL SECURITY')
    op.drop_table("idempotency_records")
