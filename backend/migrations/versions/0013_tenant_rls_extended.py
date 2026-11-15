"""Enable PostgreSQL row-level security on remaining tenant-owned tables.

Idempotency records are already protected by migration 0005 and auth sessions
by migration 0008. Password-reset tokens are deliberately excluded because
their lookup precedes tenant-context establishment.
"""
from alembic import op

revision = "0013_tenant_rls_extended"
down_revision = "0012_currency_policy_versions"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "invoice_approvals",
    "approval_policies",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"""
            CREATE POLICY "tenant_isolation" ON "{table}"
            USING ("tenant_id" = current_setting('app.tenant_id', true))
            WITH CHECK ("tenant_id" = current_setting('app.tenant_id', true))
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in reversed(TENANT_TABLES):
        op.execute(f'DROP POLICY IF EXISTS "tenant_isolation" ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
