"""PostgreSQL integration coverage for tenant-owned relational references."""
import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="Requires disposable PostgreSQL database",
)
def test_tenant_reference_constraints_reject_cross_tenant_links_and_reassignment():
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    suffix = uuid.uuid4().hex
    tenant_a = "ref-a-" + suffix
    tenant_b = "ref-b-" + suffix
    now = datetime.now(timezone.utc)
    supplier_a = None
    supplier_b = None

    try:
        with engine.begin() as connection:
            supplier_a = connection.execute(text("""
                INSERT INTO public.suppliers (tenant_id, name, tax_id, created_at)
                VALUES (:tenant, :name, NULL, :created_at)
                RETURNING id
            """), {"tenant": tenant_a, "name": "Supplier A " + suffix, "created_at": now}).scalar_one()
            supplier_b = connection.execute(text("""
                INSERT INTO public.suppliers (tenant_id, name, tax_id, created_at)
                VALUES (:tenant, :name, NULL, :created_at)
                RETURNING id
            """), {"tenant": tenant_b, "name": "Supplier B " + suffix, "created_at": now}).scalar_one()

            connection.execute(text("""
                INSERT INTO public.purchase_orders (
                    tenant_id, po_number, supplier_id, currency, total_amount, created_at
                ) VALUES (:tenant, :po, :supplier, 'INR', 100.00, :created_at)
            """), {
                "tenant": tenant_a, "po": "PO-VALID-" + suffix,
                "supplier": supplier_a, "created_at": now,
            })

            # A tenant-A purchase order cannot point to a tenant-B supplier,
            # even when the connection bypasses row-level security.
            with pytest.raises(Exception, match="cross-tenant or missing supplier reference"):
                with connection.begin_nested():
                    connection.execute(text("""
                        INSERT INTO public.purchase_orders (
                            tenant_id, po_number, supplier_id, currency, total_amount, created_at
                        ) VALUES (:tenant, :po, :supplier, 'INR', 100.00, :created_at)
                    """), {
                        "tenant": tenant_a, "po": "PO-CROSS-" + suffix,
                        "supplier": supplier_b, "created_at": now,
                    })

            # A referenced supplier cannot be moved to another tenant while
            # a tenant-A purchase order still references it.
            with pytest.raises(Exception, match="cannot reassign supplier tenant"):
                with connection.begin_nested():
                    connection.execute(text("""
                        UPDATE public.suppliers SET tenant_id = :tenant_b
                        WHERE id = :supplier_a
                    """), {"tenant_b": tenant_b, "supplier_a": supplier_a})

            constraints = connection.execute(text("""
                SELECT conname
                FROM pg_constraint
                WHERE conname IN (
                    'fk_purchase_orders_tenant_supplier',
                    'fk_invoices_tenant_supplier',
                    'fk_invoices_tenant_purchase_order',
                    'fk_invoices_tenant_creator',
                    'fk_invoices_tenant_approval_policy',
                    'fk_approval_policies_tenant_creator',
                    'fk_invoice_approvals_tenant_invoice',
                    'fk_invoice_approvals_tenant_approver',
                    'fk_invoice_approvals_tenant_policy',
                    'fk_auth_sessions_tenant_user',
                    'fk_password_reset_tokens_tenant_user',
                    'fk_email_verification_tokens_tenant_user'
                )
            """)).scalars().all()
            assert len(constraints) == 12

    finally:
        with engine.begin() as connection:
            connection.execute(text("""
                DELETE FROM public.purchase_orders WHERE tenant_id = :tenant
            """), {"tenant": tenant_a})
            connection.execute(text("""
                DELETE FROM public.suppliers WHERE tenant_id IN (:tenant_a, :tenant_b)
            """), {"tenant_a": tenant_a, "tenant_b": tenant_b})
        engine.dispose()
