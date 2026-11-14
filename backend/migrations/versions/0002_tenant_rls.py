"""Enable PostgreSQL row-level security for tenant-owned records.

The app sets app.tenant_id transaction-locally after authenticating a user.
Requests without a tenant context see no rows and cannot insert tenant rows.
"""
from alembic import op

revision = "0002_tenant_rls"
down_revision = "0002_expand_workflows"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "suppliers",
    "purchase_orders",
    "invoices",
    "policy_documents",
    "agent_memories",
    "audit_events",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # RLS is PostgreSQL-specific. Production configuration requires PostgreSQL.
        return

    for table in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'''
            CREATE POLICY "tenant_isolation" ON "{table}"
            USING ("tenant_id" = current_setting('app.tenant_id', true))
            WITH CHECK ("tenant_id" = current_setting('app.tenant_id', true))
            '''
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in reversed(TENANT_TABLES):
        op.execute(f'DROP POLICY IF EXISTS "tenant_isolation" ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
