import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.database import SessionLocal, set_tenant_context
from app.audit import record_event, verify_event_chain
from app.models import AuditEvent


@pytest.mark.skipif(
    not os.getenv("RLS_TEST_ADMIN_DATABASE_URL"),
    reason="PostgreSQL RLS integration test requires RLS_TEST_ADMIN_DATABASE_URL",
)
def test_postgres_rls_isolates_tenants_and_reapplies_context_after_commit():
    admin_engine = create_engine(os.environ["RLS_TEST_ADMIN_DATABASE_URL"])
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    name_a = f"rls-a-{tenant_a}"
    name_b = f"rls-b-{tenant_b}"

    try:
        # Confirm the extended migration installed FORCE RLS and its tenant policy.
        with admin_engine.connect() as conn:
            protected_tables = {
                row[0]: row[1]
                for row in conn.execute(text("""
                    SELECT c.relname, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = current_schema()
                      AND c.relname IN (
                        'idempotency_records', 'invoice_approvals',
                        'approval_policies', 'auth_sessions'
                      )
                """)).all()
            }
            assert set(protected_tables) == {
                "idempotency_records", "invoice_approvals",
                "approval_policies", "auth_sessions",
            }
            assert all(protected_tables.values())
            policies = {
                (row[0], row[1])
                for row in conn.execute(text("""
                    SELECT tablename, policyname FROM pg_policies
                    WHERE schemaname = current_schema()
                """)).all()
            }
            assert {
                ("idempotency_records", "tenant_isolation"),
                ("invoice_approvals", "tenant_isolation"),
                ("approval_policies", "tenant_isolation"),
                ("auth_sessions", "auth_session_tenant_isolation"),
            }.issubset(policies)

        # Seed both tenants through the privileged migration/admin connection.
        with admin_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO suppliers (tenant_id, name, created_at) "
                    "VALUES (:tenant_a, :name_a, CURRENT_TIMESTAMP), "
                    "(:tenant_b, :name_b, CURRENT_TIMESTAMP)"
                ),
                {
                    "tenant_a": tenant_a,
                    "name_a": name_a,
                    "tenant_b": tenant_b,
                    "name_b": name_b,
                },
            )

        with SessionLocal() as db:
            # No tenant context must expose no tenant-owned rows.
            assert db.execute(
                text("SELECT name FROM suppliers WHERE name IN (:a, :b)"),
                {"a": name_a, "b": name_b},
            ).scalars().all() == []
            db.rollback()

            set_tenant_context(db, tenant_a)
            visible = db.execute(
                text("SELECT name FROM suppliers WHERE name IN (:a, :b)"),
                {"a": name_a, "b": name_b},
            ).scalars().all()
            assert visible == [name_a]

            # The context must be re-established after a transaction boundary.
            db.commit()
            visible_after_commit = db.execute(
                text("SELECT name FROM suppliers WHERE name IN (:a, :b)"),
                {"a": name_a, "b": name_b},
            ).scalars().all()
            assert visible_after_commit == [name_a]

            with pytest.raises(DBAPIError):
                db.execute(
                    text("INSERT INTO suppliers (tenant_id, name, created_at) "
                         "VALUES (:tenant, :name, CURRENT_TIMESTAMP)"),
                    {"tenant": tenant_b, "name": f"cross-tenant-{uuid.uuid4()}"},
                )
            db.rollback()
    finally:
        with admin_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM suppliers WHERE tenant_id IN (:a, :b)"),
                {"a": tenant_a, "b": tenant_b},
            )
        admin_engine.dispose()


@pytest.mark.skipif(
    not os.getenv("RLS_TEST_ADMIN_DATABASE_URL"),
    reason="PostgreSQL RLS integration test requires RLS_TEST_ADMIN_DATABASE_URL",
)
def test_postgres_rls_isolates_invoice_approvals_and_approval_policies():
    """Exercise row visibility and WITH CHECK for both tables added in 0013."""
    admin_engine = create_engine(os.environ["RLS_TEST_ADMIN_DATABASE_URL"])
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    user_a_email = f"rls-a-{tenant_a}@example.test"
    user_b_email = f"rls-b-{tenant_b}@example.test"
    invoice_a_number = f"RLS-A-{tenant_a}"
    invoice_b_number = f"RLS-B-{tenant_b}"

    try:
        with admin_engine.begin() as conn:
            users = conn.execute(
                text("""
                    INSERT INTO users (
                        tenant_id, tenant_name, email, hashed_password, role,
                        is_active, mfa_enabled, created_at
                    )
                    VALUES
                        (:tenant_a, 'RLS A', :email_a, 'not-a-real-password-hash',
                         'analyst', TRUE, FALSE, CURRENT_TIMESTAMP),
                        (:tenant_b, 'RLS B', :email_b, 'not-a-real-password-hash',
                         'analyst', TRUE, FALSE, CURRENT_TIMESTAMP)
                    RETURNING id, tenant_id
                """),
                {
                    "tenant_a": tenant_a,
                    "tenant_b": tenant_b,
                    "email_a": user_a_email,
                    "email_b": user_b_email,
                },
            ).all()
            user_ids = {row.tenant_id: row.id for row in users}

            invoices = conn.execute(
                text("""
                    INSERT INTO invoices (
                        tenant_id, invoice_number, status, extraction_fields,
                        reconciliation, created_by_user_id, created_at
                    )
                    VALUES
                        (:tenant_a, :invoice_a, 'received', '{}'::json,
                         '{}'::json, :user_a, CURRENT_TIMESTAMP),
                        (:tenant_b, :invoice_b, 'received', '{}'::json,
                         '{}'::json, :user_b, CURRENT_TIMESTAMP)
                    RETURNING id, tenant_id
                """),
                {
                    "tenant_a": tenant_a,
                    "tenant_b": tenant_b,
                    "invoice_a": invoice_a_number,
                    "invoice_b": invoice_b_number,
                    "user_a": user_ids[tenant_a],
                    "user_b": user_ids[tenant_b],
                },
            ).all()
            invoice_ids = {row.tenant_id: row.id for row in invoices}

            conn.execute(
                text("""
                    INSERT INTO invoice_approvals (
                        tenant_id, invoice_id, reviewer_user_id, created_at
                    )
                    VALUES
                        (:tenant_a, :invoice_a, :user_a, CURRENT_TIMESTAMP),
                        (:tenant_b, :invoice_b, :user_b, CURRENT_TIMESTAMP)
                """),
                {
                    "tenant_a": tenant_a,
                    "tenant_b": tenant_b,
                    "invoice_a": invoice_ids[tenant_a],
                    "invoice_b": invoice_ids[tenant_b],
                    "user_a": user_ids[tenant_a],
                    "user_b": user_ids[tenant_b],
                },
            )
            conn.execute(
                text("""
                    INSERT INTO approval_policies (
                        tenant_id, version, currency, threshold_amount,
                        required_approvals, created_by_user_id, created_at
                    )
                    VALUES
                        (:tenant_a, 1, 'INR', 1000, 2, :user_a, CURRENT_TIMESTAMP),
                        (:tenant_b, 1, 'INR', 2000, 2, :user_b, CURRENT_TIMESTAMP)
                """),
                {
                    "tenant_a": tenant_a,
                    "tenant_b": tenant_b,
                    "user_a": user_ids[tenant_a],
                    "user_b": user_ids[tenant_b],
                },
            )

        with SessionLocal() as db:
            set_tenant_context(db, tenant_a)
            visible_approvals = db.execute(
                text("""
                    SELECT ia.tenant_id, i.invoice_number
                    FROM invoice_approvals ia
                    JOIN invoices i ON i.id = ia.invoice_id
                    WHERE ia.tenant_id IN (:tenant_a, :tenant_b)
                """),
                {"tenant_a": tenant_a, "tenant_b": tenant_b},
            ).all()
            assert [(row.tenant_id, row.invoice_number) for row in visible_approvals] == [
                (tenant_a, invoice_a_number)
            ]

            visible_policies = db.execute(
                text("""
                    SELECT tenant_id, threshold_amount
                    FROM approval_policies
                    WHERE tenant_id IN (:tenant_a, :tenant_b)
                """),
                {"tenant_a": tenant_a, "tenant_b": tenant_b},
            ).all()
            assert [(row.tenant_id, float(row.threshold_amount)) for row in visible_policies] == [
                (tenant_a, 1000.0)
            ]

            # WITH CHECK must reject writes explicitly targeting another tenant.
            with pytest.raises(DBAPIError):
                db.execute(
                    text("""
                        INSERT INTO approval_policies (
                            tenant_id, version, currency, threshold_amount,
                            required_approvals, created_by_user_id, created_at
                        )
                        VALUES (:tenant_b, 2, 'INR', 3000, 2, :user_b, CURRENT_TIMESTAMP)
                    """),
                    {"tenant_b": tenant_b, "user_b": user_ids[tenant_b]},
                )
            db.rollback()

            # Verify the second table's write policy independently.
            set_tenant_context(db, tenant_a)
            with pytest.raises(DBAPIError):
                db.execute(
                    text("""
                        INSERT INTO invoice_approvals (
                            tenant_id, invoice_id, reviewer_user_id, created_at
                        )
                        VALUES (:tenant_b, :invoice_b, :user_b, CURRENT_TIMESTAMP)
                    """),
                    {
                        "tenant_b": tenant_b,
                        "invoice_b": invoice_ids[tenant_b],
                        "user_b": user_ids[tenant_b],
                    },
                )
            db.rollback()
    finally:
        with admin_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM invoice_approvals WHERE tenant_id IN (:a, :b)"),
                {"a": tenant_a, "b": tenant_b},
            )
            conn.execute(
                text("DELETE FROM approval_policies WHERE tenant_id IN (:a, :b)"),
                {"a": tenant_a, "b": tenant_b},
            )
            conn.execute(
                text("DELETE FROM invoices WHERE tenant_id IN (:a, :b)"),
                {"a": tenant_a, "b": tenant_b},
            )
            conn.execute(
                text("DELETE FROM users WHERE tenant_id IN (:a, :b)"),
                {"a": tenant_a, "b": tenant_b},
            )
        admin_engine.dispose()


@pytest.mark.skipif(
    not os.getenv("RLS_TEST_ADMIN_DATABASE_URL"),
    reason="PostgreSQL audit integration test requires RLS_TEST_ADMIN_DATABASE_URL",
)
def test_audit_hash_chain_and_database_immutability():
    tenant_id = str(uuid.uuid4())
    actor = SimpleNamespace(tenant_id=tenant_id, id=987654321)

    with SessionLocal() as db:
        set_tenant_context(db, tenant_id)
        first = record_event(db, actor, "invoice", "audit-test-1", "created", {"amount": 100})
        db.flush()
        second = record_event(db, actor, "invoice", "audit-test-1", "reviewed", {"reviewer": "test"})
        db.commit()

        events = (
            db.query(AuditEvent)
            .filter(AuditEvent.tenant_id == tenant_id)
            .order_by(AuditEvent.id.asc())
            .all()
        )
        assert len(events) == 2
        assert verify_event_chain(events) == (True, None)

        events[0].action = "tampered"
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
