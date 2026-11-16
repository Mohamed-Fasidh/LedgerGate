"""Reject foreign-key references that cross tenant boundaries.

Ordinary foreign keys guarantee that a parent row exists, but do not guarantee
that its tenant_id matches the child row. These triggers add that invariant at
the database boundary for all tenant-owned business references.
"""
from alembic import op

revision = "0027_tenant_owned_references"
down_revision = "0026_preauth_identity_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # Refuse to install the guard over already-corrupt relationships.
    # Operators must reconcile these rows explicitly rather than silently
    # carrying cross-tenant references into the protected schema.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM public.purchase_orders child
                LEFT JOIN public.suppliers parent ON parent.id = child.supplier_id
                WHERE parent.id IS NULL OR parent.tenant_id <> child.tenant_id
            ) OR EXISTS (
                SELECT 1 FROM public.invoices child
                LEFT JOIN public.suppliers parent ON parent.id = child.supplier_id
                WHERE child.supplier_id IS NOT NULL
                  AND (parent.id IS NULL OR parent.tenant_id <> child.tenant_id)
            ) OR EXISTS (
                SELECT 1 FROM public.invoices child
                LEFT JOIN public.purchase_orders parent ON parent.id = child.purchase_order_id
                WHERE child.purchase_order_id IS NOT NULL
                  AND (parent.id IS NULL OR parent.tenant_id <> child.tenant_id)
            ) OR EXISTS (
                SELECT 1 FROM public.invoices child
                LEFT JOIN public.users parent ON parent.id = child.created_by_user_id
                WHERE child.created_by_user_id IS NOT NULL
                  AND (parent.id IS NULL OR parent.tenant_id <> child.tenant_id)
            ) OR EXISTS (
                SELECT 1 FROM public.invoices child
                LEFT JOIN public.approval_policies parent ON parent.id = child.approval_policy_id
                WHERE child.approval_policy_id IS NOT NULL
                  AND (parent.id IS NULL OR parent.tenant_id <> child.tenant_id)
            ) OR EXISTS (
                SELECT 1 FROM public.approval_policies child
                LEFT JOIN public.users parent ON parent.id = child.created_by_user_id
                WHERE child.created_by_user_id IS NOT NULL
                  AND (parent.id IS NULL OR parent.tenant_id <> child.tenant_id)
            ) OR EXISTS (
                SELECT 1 FROM public.invoice_approvals child
                LEFT JOIN public.invoices parent ON parent.id = child.invoice_id
                WHERE parent.id IS NULL OR parent.tenant_id <> child.tenant_id
            ) OR EXISTS (
                SELECT 1 FROM public.invoice_approvals child
                LEFT JOIN public.users parent ON parent.id = child.approver_user_id
                WHERE parent.id IS NULL OR parent.tenant_id <> child.tenant_id
            ) OR EXISTS (
                SELECT 1 FROM public.invoice_approvals child
                LEFT JOIN public.approval_policies parent ON parent.id = child.policy_id
                WHERE child.policy_id IS NOT NULL
                  AND (parent.id IS NULL OR parent.tenant_id <> child.tenant_id)
            ) OR EXISTS (
                SELECT 1 FROM public.auth_sessions child
                LEFT JOIN public.users parent ON parent.id = child.user_id
                WHERE parent.id IS NULL OR parent.tenant_id <> child.tenant_id
            ) OR EXISTS (
                SELECT 1 FROM public.password_reset_tokens child
                LEFT JOIN public.users parent ON parent.id = child.user_id
                WHERE parent.id IS NULL OR parent.tenant_id <> child.tenant_id
            ) OR EXISTS (
                SELECT 1 FROM public.email_verification_tokens child
                LEFT JOIN public.users parent ON parent.id = child.user_id
                WHERE parent.id IS NULL OR parent.tenant_id <> child.tenant_id
            ) THEN
                RAISE EXCEPTION 'Existing cross-tenant references must be reconciled before migration 0027';
            END IF;
        END;
        $$;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION public.ledgergate_enforce_same_tenant_reference()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_TABLE_NAME = 'purchase_orders' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM public.suppliers
                    WHERE id = NEW.supplier_id AND tenant_id = NEW.tenant_id
                ) THEN
                    RAISE EXCEPTION 'cross-tenant or missing supplier reference'
                        USING ERRCODE = '23503';
                END IF;

            ELSIF TG_TABLE_NAME = 'invoices' THEN
                IF NEW.supplier_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.suppliers
                    WHERE id = NEW.supplier_id AND tenant_id = NEW.tenant_id
                ) THEN
                    RAISE EXCEPTION 'cross-tenant or missing invoice supplier reference'
                        USING ERRCODE = '23503';
                END IF;
                IF NEW.purchase_order_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.purchase_orders
                    WHERE id = NEW.purchase_order_id AND tenant_id = NEW.tenant_id
                ) THEN
                    RAISE EXCEPTION 'cross-tenant or missing invoice purchase-order reference'
                        USING ERRCODE = '23503';
                END IF;
                IF NEW.created_by_user_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.users
                    WHERE id = NEW.created_by_user_id AND tenant_id = NEW.tenant_id
                ) THEN
                    RAISE EXCEPTION 'cross-tenant or missing invoice creator reference'
                        USING ERRCODE = '23503';
                END IF;
                IF NEW.approval_policy_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.approval_policies
                    WHERE id = NEW.approval_policy_id AND tenant_id = NEW.tenant_id
                ) THEN
                    RAISE EXCEPTION 'cross-tenant or missing invoice approval-policy reference'
                        USING ERRCODE = '23503';
                END IF;

            ELSIF TG_TABLE_NAME = 'approval_policies' THEN
                IF NEW.created_by_user_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.users
                    WHERE id = NEW.created_by_user_id AND tenant_id = NEW.tenant_id
                ) THEN
                    RAISE EXCEPTION 'cross-tenant or missing approval-policy creator reference'
                        USING ERRCODE = '23503';
                END IF;

            ELSIF TG_TABLE_NAME IN ('auth_sessions', 'password_reset_tokens', 'email_verification_tokens') THEN
                IF NOT EXISTS (
                    SELECT 1 FROM public.users
                    WHERE id = NEW.user_id AND tenant_id = NEW.tenant_id
                ) THEN
                    RAISE EXCEPTION 'cross-tenant or missing identity user reference'
                        USING ERRCODE = '23503';
                END IF;

            ELSIF TG_TABLE_NAME = 'invoice_approvals' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM public.invoices
                    WHERE id = NEW.invoice_id AND tenant_id = NEW.tenant_id
                ) THEN
                    RAISE EXCEPTION 'cross-tenant or missing invoice-approval invoice reference'
                        USING ERRCODE = '23503';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM public.users
                    WHERE id = NEW.approver_user_id AND tenant_id = NEW.tenant_id
                ) THEN
                    RAISE EXCEPTION 'cross-tenant or missing invoice-approval user reference'
                        USING ERRCODE = '23503';
                END IF;
                IF NEW.policy_id IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM public.approval_policies
                    WHERE id = NEW.policy_id AND tenant_id = NEW.tenant_id
                ) THEN
                    RAISE EXCEPTION 'cross-tenant or missing invoice-approval policy reference'
                        USING ERRCODE = '23503';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)

    for table in (
        "purchase_orders",
        "invoices",
        "approval_policies",
        "invoice_approvals",
        "auth_sessions",
        "password_reset_tokens",
        "email_verification_tokens",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS ledgergate_same_tenant_reference ON public.{table}")
        op.execute(f"""
            CREATE TRIGGER ledgergate_same_tenant_reference
            BEFORE INSERT OR UPDATE
            ON public.{table}
            FOR EACH ROW
            EXECUTE FUNCTION public.ledgergate_enforce_same_tenant_reference()
        """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in ("invoice_approvals", "approval_policies", "invoices", "purchase_orders"):
        op.execute(f"DROP TRIGGER IF EXISTS ledgergate_same_tenant_reference ON public.{table}")
    op.execute("DROP FUNCTION IF EXISTS public.ledgergate_enforce_same_tenant_reference()")
