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


    # Protect the other direction as well: a referenced parent must not be
    # reassigned to a different tenant after valid child rows already exist.
    op.execute("""
        CREATE OR REPLACE FUNCTION public.ledgergate_prevent_tenant_reassignment()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.tenant_id IS NOT DISTINCT FROM NEW.tenant_id THEN
                RETURN NEW;
            END IF;

            IF TG_TABLE_NAME = 'users' THEN
                IF EXISTS (SELECT 1 FROM public.invoices WHERE created_by_user_id = OLD.id AND tenant_id <> NEW.tenant_id)
                   OR EXISTS (SELECT 1 FROM public.approval_policies WHERE created_by_user_id = OLD.id AND tenant_id <> NEW.tenant_id)
                   OR EXISTS (SELECT 1 FROM public.invoice_approvals WHERE approver_user_id = OLD.id AND tenant_id <> NEW.tenant_id)
                   OR EXISTS (SELECT 1 FROM public.auth_sessions WHERE user_id = OLD.id AND tenant_id <> NEW.tenant_id)
                   OR EXISTS (SELECT 1 FROM public.password_reset_tokens WHERE user_id = OLD.id AND tenant_id <> NEW.tenant_id)
                   OR EXISTS (SELECT 1 FROM public.email_verification_tokens WHERE user_id = OLD.id AND tenant_id <> NEW.tenant_id) THEN
                    RAISE EXCEPTION 'cannot reassign user tenant while tenant-owned references exist'
                        USING ERRCODE = '23514';
                END IF;

            ELSIF TG_TABLE_NAME = 'suppliers' THEN
                IF EXISTS (SELECT 1 FROM public.purchase_orders WHERE supplier_id = OLD.id AND tenant_id <> NEW.tenant_id)
                   OR EXISTS (SELECT 1 FROM public.invoices WHERE supplier_id = OLD.id AND tenant_id <> NEW.tenant_id) THEN
                    RAISE EXCEPTION 'cannot reassign supplier tenant while tenant-owned references exist'
                        USING ERRCODE = '23514';
                END IF;

            ELSIF TG_TABLE_NAME = 'purchase_orders' THEN
                IF EXISTS (SELECT 1 FROM public.invoices WHERE purchase_order_id = OLD.id AND tenant_id <> NEW.tenant_id) THEN
                    RAISE EXCEPTION 'cannot reassign purchase-order tenant while tenant-owned references exist'
                        USING ERRCODE = '23514';
                END IF;

            ELSIF TG_TABLE_NAME = 'approval_policies' THEN
                IF EXISTS (SELECT 1 FROM public.invoices WHERE approval_policy_id = OLD.id AND tenant_id <> NEW.tenant_id)
                   OR EXISTS (SELECT 1 FROM public.invoice_approvals WHERE policy_id = OLD.id AND tenant_id <> NEW.tenant_id) THEN
                    RAISE EXCEPTION 'cannot reassign approval-policy tenant while tenant-owned references exist'
                        USING ERRCODE = '23514';
                END IF;

            ELSIF TG_TABLE_NAME = 'invoices' THEN
                IF EXISTS (SELECT 1 FROM public.invoice_approvals WHERE invoice_id = OLD.id AND tenant_id <> NEW.tenant_id) THEN
                    RAISE EXCEPTION 'cannot reassign invoice tenant while tenant-owned references exist'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)

    for table in ("users", "suppliers", "purchase_orders", "approval_policies", "invoices"):
        op.execute(f"DROP TRIGGER IF EXISTS ledgergate_prevent_tenant_reassignment ON public.{table}")
        op.execute(f"""
            CREATE TRIGGER ledgergate_prevent_tenant_reassignment
            BEFORE UPDATE OF tenant_id
            ON public.{table}
            FOR EACH ROW
            EXECUTE FUNCTION public.ledgergate_prevent_tenant_reassignment()
        """)

    # Composite unique keys and foreign keys make tenant ownership a native
    # relational invariant. Unlike trigger-only checks, PostgreSQL's FK
    # machinery enforces these relationships atomically under concurrency and
    # also prevents a referenced parent from changing tenant_id.
    for table in ("users", "suppliers", "purchase_orders", "approval_policies", "invoices"):
        op.create_unique_constraint(
            f"uq_{table}_tenant_id_id",
            table,
            ["tenant_id", "id"],
        )

    tenant_foreign_keys = (
        ("purchase_orders", "fk_purchase_orders_tenant_supplier", ["tenant_id", "supplier_id"], "suppliers", ["tenant_id", "id"]),
        ("invoices", "fk_invoices_tenant_supplier", ["tenant_id", "supplier_id"], "suppliers", ["tenant_id", "id"]),
        ("invoices", "fk_invoices_tenant_purchase_order", ["tenant_id", "purchase_order_id"], "purchase_orders", ["tenant_id", "id"]),
        ("invoices", "fk_invoices_tenant_creator", ["tenant_id", "created_by_user_id"], "users", ["tenant_id", "id"]),
        ("invoices", "fk_invoices_tenant_approval_policy", ["tenant_id", "approval_policy_id"], "approval_policies", ["tenant_id", "id"]),
        ("approval_policies", "fk_approval_policies_tenant_creator", ["tenant_id", "created_by_user_id"], "users", ["tenant_id", "id"]),
        ("invoice_approvals", "fk_invoice_approvals_tenant_invoice", ["tenant_id", "invoice_id"], "invoices", ["tenant_id", "id"]),
        ("invoice_approvals", "fk_invoice_approvals_tenant_approver", ["tenant_id", "approver_user_id"], "users", ["tenant_id", "id"]),
        ("invoice_approvals", "fk_invoice_approvals_tenant_policy", ["tenant_id", "policy_id"], "approval_policies", ["tenant_id", "id"]),
        ("auth_sessions", "fk_auth_sessions_tenant_user", ["tenant_id", "user_id"], "users", ["tenant_id", "id"]),
        ("password_reset_tokens", "fk_password_reset_tokens_tenant_user", ["tenant_id", "user_id"], "users", ["tenant_id", "id"]),
        ("email_verification_tokens", "fk_email_verification_tokens_tenant_user", ["tenant_id", "user_id"], "users", ["tenant_id", "id"]),
    )
    for source_table, constraint_name, source_columns, target_table, target_columns in tenant_foreign_keys:
        op.create_foreign_key(
            constraint_name,
            source_table,
            target_table,
            source_columns,
            target_columns,
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, constraint_name in (
        ("email_verification_tokens", "fk_email_verification_tokens_tenant_user"),
        ("password_reset_tokens", "fk_password_reset_tokens_tenant_user"),
        ("auth_sessions", "fk_auth_sessions_tenant_user"),
        ("invoice_approvals", "fk_invoice_approvals_tenant_policy"),
        ("invoice_approvals", "fk_invoice_approvals_tenant_approver"),
        ("invoice_approvals", "fk_invoice_approvals_tenant_invoice"),
        ("approval_policies", "fk_approval_policies_tenant_creator"),
        ("invoices", "fk_invoices_tenant_approval_policy"),
        ("invoices", "fk_invoices_tenant_creator"),
        ("invoices", "fk_invoices_tenant_purchase_order"),
        ("invoices", "fk_invoices_tenant_supplier"),
        ("purchase_orders", "fk_purchase_orders_tenant_supplier"),
    ):
        op.drop_constraint(constraint_name, table, type_="foreignkey")
    for table in ("invoices", "approval_policies", "purchase_orders", "suppliers", "users"):
        op.drop_constraint(f"uq_{table}_tenant_id_id", table, type_="unique")

    for table in (
        "email_verification_tokens",
        "password_reset_tokens",
        "auth_sessions",
        "invoice_approvals",
        "approval_policies",
        "invoices",
        "purchase_orders",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS ledgergate_same_tenant_reference ON public.{table}")
    for table in ("invoices", "approval_policies", "purchase_orders", "suppliers", "users"):
        op.execute(f"DROP TRIGGER IF EXISTS ledgergate_prevent_tenant_reassignment ON public.{table}")
    op.execute("DROP FUNCTION IF EXISTS public.ledgergate_prevent_tenant_reassignment()")
    op.execute("DROP FUNCTION IF EXISTS public.ledgergate_enforce_same_tenant_reference()")
