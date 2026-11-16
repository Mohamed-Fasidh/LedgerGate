"""Add tenant-scoped idempotency response storage."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0011_idempotency_records"
down_revision = "0010_auth_sessions"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "idempotency_records" not in inspect(bind).get_table_names():
        op.create_table(
            "idempotency_records",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.String(64), nullable=False),
            sa.Column("key", sa.String(128), nullable=False),
            sa.Column("operation", sa.String(100), nullable=False),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column("response_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("tenant_id", "key", name="uq_idempotency_tenant_key"),
        )
        op.create_index("ix_idempotency_records_tenant_id", "idempotency_records", ["tenant_id"])
        op.create_index("ix_idempotency_records_created_at", "idempotency_records", ["created_at"])
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE idempotency_records ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE idempotency_records FORCE ROW LEVEL SECURITY")
        op.execute("CREATE POLICY tenant_isolation ON idempotency_records USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')) WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), ''))")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON idempotency_records")
        op.execute("ALTER TABLE idempotency_records NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE idempotency_records DISABLE ROW LEVEL SECURITY")
    if "idempotency_records" in inspect(bind).get_table_names():
        op.drop_table("idempotency_records")
