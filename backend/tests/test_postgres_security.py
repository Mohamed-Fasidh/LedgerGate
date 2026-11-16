import os
import uuid
import hashlib
import pytest
from sqlalchemy import create_engine, text, UniqueConstraint
from sqlalchemy.orm import sessionmaker

@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_rls_blocks_cross_tenant_and_audit_is_append_only():
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    Session = sessionmaker(bind=engine)
    event_id = str(uuid.uuid4())

    # The CI connection is a migration/admin account. Execute application queries
    # as a non-owner role so PostgreSQL RLS is actually exercised.
    with Session() as db:
        db.execute(text("SET LOCAL ROLE ledgergate_runtime"))
        db.execute(text("SELECT set_config('app.tenant_id', 'tenant-a', false)"))
        db.execute(text("""
            INSERT INTO audit_events
              (tenant_id, actor_user_id, entity_type, entity_id, action, details, created_at, previous_hash, event_hash)
            VALUES
              ('tenant-a', NULL, 'test', :event_id, 'created', '{}'::json, now(), :previous_hash, :event_hash)
        """), {"event_id": event_id, "previous_hash": "0" * 64, "event_hash": hashlib.sha256(event_id.encode()).hexdigest()})
        db.execute(text("""
            INSERT INTO approval_policies
              (tenant_id, name, min_amount, max_amount, approval_steps, is_active, created_by_user_id, created_at)
            VALUES
              ('tenant-a', :policy_name, 0, NULL, '[["reviewer"],["admin"]]'::json, true, NULL, now())
        """), {"policy_name": f"rls-test-{event_id}"})
        db.execute(text("""
            INSERT INTO idempotency_records
              (tenant_id, key, operation, request_hash, response_json, created_at)
            VALUES
              ('tenant-a', :idem_key, 'supplier.create', :request_hash, CAST(:response_json AS json), now())
        """), {"idem_key": f"idem-{event_id}", "request_hash": "a" * 64, "response_json": '{"id":1,"name":"Tenant A"}'})
        db.commit()

    with Session() as db:
        db.execute(text("SET LOCAL ROLE ledgergate_runtime"))
        db.execute(text("SELECT set_config('app.tenant_id', 'tenant-b', false)"))
        visible = db.execute(
            text("SELECT count(*) FROM audit_events WHERE entity_id=:event_id"),
            {"event_id": event_id},
        ).scalar_one()
        assert visible == 0
        policy_visible = db.execute(
            text("SELECT count(*) FROM approval_policies WHERE name=:policy_name"),
            {"policy_name": f"rls-test-{event_id}"},
        ).scalar_one()
        assert policy_visible == 0
        idem_visible = db.execute(
            text("SELECT count(*) FROM idempotency_records WHERE key=:idem_key"),
            {"idem_key": f"idem-{event_id}"},
        ).scalar_one()
        assert idem_visible == 0
        db.rollback()

    with Session() as db:
        db.execute(text("SET LOCAL ROLE ledgergate_runtime"))
        db.execute(text("SELECT set_config('app.tenant_id', 'tenant-a', false)"))
        with pytest.raises(Exception, match="append-only"):
            db.execute(
                text("UPDATE audit_events SET action='tampered' WHERE entity_id=:event_id"),
                {"event_id": event_id},
            )
        db.rollback()
    engine.dispose()



@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_every_tenant_table_has_forced_rls_and_tenant_policy():
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    expected_tables = (
        "suppliers",
        "purchase_orders",
        "invoices",
        "policy_documents",
        "agent_memories",
        "audit_events",
        "approval_policies",
        "invoice_approvals",
        "auth_sessions",
        "idempotency_records",
        "users",
        "password_reset_tokens",
        "email_verification_tokens",
    )
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                       p.cmd AS policy_command,
                       p.qual AS policy_using,
                       p.with_check AS policy_with_check
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_policies p
                  ON p.schemaname = n.nspname
                 AND p.tablename = c.relname
                 AND p.policyname = 'tenant_isolation'
                WHERE n.nspname = current_schema()
                  AND c.relname = ANY(:table_names)
            """), {"table_names": list(expected_tables)}).mappings().all()

        actual = {row["relname"]: row for row in rows}
        assert set(actual) == set(expected_tables)
        for table in expected_tables:
            assert actual[table]["relrowsecurity"] is True, f"{table} must enable RLS"
            assert actual[table]["relforcerowsecurity"] is True, f"{table} must FORCE RLS"
            policy = actual[table]
            assert policy["policy_command"] == "ALL", f"{table} policy must cover all commands"
            for expression_name in ("policy_using", "policy_with_check"):
                expression = (policy[expression_name] or "").lower()
                assert "tenant_id" in expression, f"{table} {expression_name} must constrain tenant_id"
                assert "current_setting" in expression, f"{table} {expression_name} must use the active tenant context"
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_runtime_role_cannot_insert_or_move_rows_into_another_tenant():
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    Session = sessionmaker(bind=engine)
    suffix = str(uuid.uuid4())
    supplier_name = f"rls-write-{suffix}"

    try:
        with Session() as db:
            db.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            db.execute(text("SELECT set_config('app.tenant_id', 'tenant-a', false)"))
            db.execute(text("""
                INSERT INTO suppliers (tenant_id, name, tax_id, created_at)
                VALUES ('tenant-a', :name, NULL, now())
            """), {"name": supplier_name})
            db.commit()

        with Session() as db:
            db.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            db.execute(text("SELECT set_config('app.tenant_id', 'tenant-a', false)"))
            with pytest.raises(Exception, match="row-level security"):
                db.execute(text("""
                    INSERT INTO suppliers (tenant_id, name, tax_id, created_at)
                    VALUES ('tenant-b', :name, NULL, now())
                """), {"name": f"{supplier_name}-cross-tenant"})
            db.rollback()

        with Session() as db:
            db.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            db.execute(text("SELECT set_config('app.tenant_id', 'tenant-a', false)"))
            with pytest.raises(Exception, match="row-level security"):
                db.execute(text("""
                    UPDATE suppliers SET tenant_id = 'tenant-b'
                    WHERE tenant_id = 'tenant-a' AND name = :name
                """), {"name": supplier_name})
            db.rollback()
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_runtime_role_uses_explicit_grants_without_future_default_access():
    """New tables must not silently inherit application-role privileges."""
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    future_table = "runtime_acl_future_" + uuid.uuid4().hex
    expected_application_tables = {
        "users",
        "suppliers",
        "purchase_orders",
        "invoices",
        "policy_documents",
        "agent_memories",
        "audit_events",
        "registration_attempts",
        "login_attempts",
        "approval_policies",
        "invoice_approvals",
        "auth_sessions",
        "idempotency_records",
        "password_reset_tokens",
        "email_verification_tokens",
        "outbound_emails",
    }

    try:
        with engine.begin() as connection:
            defaults = connection.execute(text("""
                SELECT owner.rolname, defaults.defaclobjtype
                FROM pg_default_acl AS defaults
                CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl
                JOIN pg_roles AS grantee ON grantee.oid = acl.grantee
                JOIN pg_roles AS owner ON owner.oid = defaults.defaclrole
                WHERE defaults.defaclnamespace = 'public'::regnamespace
                  AND defaults.defaclobjtype IN ('r', 'S')
                  AND grantee.rolname = 'ledgergate_runtime'
            """)).all()
            assert defaults == []

            for table in expected_application_tables:
                privileges = connection.execute(text("""
                    SELECT
                      has_table_privilege('ledgergate_runtime', :table_name, 'SELECT'),
                      has_table_privilege('ledgergate_runtime', :table_name, 'INSERT'),
                      has_table_privilege('ledgergate_runtime', :table_name, 'UPDATE'),
                      has_table_privilege('ledgergate_runtime', :table_name, 'DELETE')
                """), {"table_name": f"public.{table}"}).one()
                assert tuple(privileges) == (True, True, True, True), table

            migration_table = connection.execute(text("""
                SELECT
                  has_table_privilege('ledgergate_runtime', 'public.alembic_version', 'SELECT'),
                  has_table_privilege('ledgergate_runtime', 'public.alembic_version', 'INSERT'),
                  has_table_privilege('ledgergate_runtime', 'public.alembic_version', 'UPDATE'),
                  has_table_privilege('ledgergate_runtime', 'public.alembic_version', 'DELETE')
            """)).one()
            assert tuple(migration_table) == (True, False, False, False)

            connection.execute(text(f'CREATE TABLE public."{future_table}" (id integer PRIMARY KEY)'))
            future_access = connection.execute(text("""
                SELECT
                  has_table_privilege('ledgergate_runtime', :table_name, 'SELECT'),
                  has_table_privilege('ledgergate_runtime', :table_name, 'INSERT'),
                  has_table_privilege('ledgergate_runtime', :table_name, 'UPDATE'),
                  has_table_privilege('ledgergate_runtime', :table_name, 'DELETE')
            """), {"table_name": f"public.{future_table}"}).one()
            assert tuple(future_access) == (False, False, False, False)
            connection.execute(text(f'DROP TABLE public."{future_table}"'))
    finally:
        engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_runtime_role_cannot_create_purchase_order_with_another_tenants_supplier():
    """A valid global FK must not permit a cross-tenant business reference."""
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    Session = sessionmaker(bind=engine)
    suffix = uuid.uuid4().hex
    tenant_a = "reference-a-" + suffix
    tenant_b = "reference-b-" + suffix
    supplier_a = "reference-supplier-a-" + suffix
    supplier_b = "reference-supplier-b-" + suffix
    supplier_a_id = None
    supplier_b_id = None

    try:
        with engine.begin() as admin:
            supplier_a_id = admin.execute(text("""
                INSERT INTO public.suppliers (tenant_id, name, tax_id, created_at)
                VALUES (:tenant, :name, NULL, now())
                RETURNING id
            """), {"tenant": tenant_a, "name": supplier_a}).scalar_one()
            supplier_b_id = admin.execute(text("""
                INSERT INTO public.suppliers (tenant_id, name, tax_id, created_at)
                VALUES (:tenant, :name, NULL, now())
                RETURNING id
            """), {"tenant": tenant_b, "name": supplier_b}).scalar_one()

        # A same-tenant reference remains valid for the application role.
        with Session() as runtime:
            runtime.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            runtime.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": tenant_a})
            runtime.execute(text("""
                INSERT INTO public.purchase_orders
                    (tenant_id, po_number, supplier_id, currency, total_amount, description, created_at)
                VALUES
                    (:tenant, :po_number, :supplier_id, 'INR', 100.00, NULL, now())
            """), {"tenant": tenant_a, "po_number": "valid-" + suffix, "supplier_id": supplier_a_id})
            runtime.commit()

        # The referenced supplier exists globally, but belongs to tenant B.
        # The database trigger must reject this even if an API route forgets
        # its tenant-scoped supplier lookup.
        with Session() as runtime:
            runtime.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            runtime.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": tenant_a})
            with pytest.raises(Exception, match="cross-tenant or missing supplier reference"):
                runtime.execute(text("""
                    INSERT INTO public.purchase_orders
                        (tenant_id, po_number, supplier_id, currency, total_amount, description, created_at)
                    VALUES
                        (:tenant, :po_number, :supplier_id, 'INR', 100.00, NULL, now())
                """), {"tenant": tenant_a, "po_number": "invalid-" + suffix, "supplier_id": supplier_b_id})
            runtime.rollback()
    finally:
        with engine.begin() as admin:
            admin.execute(text("DELETE FROM public.purchase_orders WHERE po_number = :po_number"),
                          {"po_number": "valid-" + suffix})
            ids = [value for value in (supplier_a_id, supplier_b_id) if value is not None]
            if ids:
                admin.execute(text("DELETE FROM public.suppliers WHERE id = ANY(:ids)"), {"ids": ids})
        engine.dispose()


def test_tenant_reference_migration_covers_all_business_relationship_tables():
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0027_tenant_owned_references.py"
    ).read_text(encoding="utf-8")
    assert "ledgergate_enforce_same_tenant_reference" in migration
    assert "DO $$" in migration
    assert "\n        $$;" in migration
    guarded_tables = (
        "purchase_orders",
        "invoices",
        "approval_policies",
        "invoice_approvals",
        "auth_sessions",
        "password_reset_tokens",
        "email_verification_tokens",
    )
    for table in guarded_tables:
        assert f'"{table}"' in migration
    assert "BEFORE INSERT OR UPDATE" in migration
    assert "USING ERRCODE = '23503'" in migration
    assert "Existing cross-tenant references must be reconciled" in migration
    assert "cross-tenant or missing identity user reference" in migration
    assert "ledgergate_prevent_tenant_reassignment" in migration
    assert "cannot reassign supplier tenant while tenant-owned references exist" in migration
    assert "cannot reassign user tenant while tenant-owned references exist" in migration
    for parent_table in ("users", "suppliers", "purchase_orders", "approval_policies", "invoices"):
        assert f'"{parent_table}"' in migration
    assert "LEFT JOIN public.suppliers" in migration
    assert "LEFT JOIN public.purchase_orders" in migration
    assert "LEFT JOIN public.users" in migration
    assert "LEFT JOIN public.approval_policies" in migration
    # Native composite foreign keys close the concurrency gap that trigger-only
    # SELECT checks cannot guarantee under simultaneous parent/child writes.
    for constraint_name in (
        "fk_purchase_orders_tenant_supplier",
        "fk_invoices_tenant_supplier",
        "fk_invoices_tenant_purchase_order",
        "fk_invoices_tenant_creator",
        "fk_invoices_tenant_approval_policy",
        "fk_approval_policies_tenant_creator",
        "fk_invoice_approvals_tenant_invoice",
        "fk_invoice_approvals_tenant_approver",
        "fk_invoice_approvals_tenant_policy",
        "fk_auth_sessions_tenant_user",
        "fk_password_reset_tokens_tenant_user",
        "fk_email_verification_tokens_tenant_user",
    ):
        assert constraint_name in migration
    assert 'f"uq_{table}_tenant_id_id"' in migration
    assert 'for table in ("users", "suppliers", "purchase_orders", "approval_policies", "invoices")' in migration

def test_orm_metadata_declares_tenant_composite_constraints():
    """Keep SQLAlchemy metadata aligned with migration 0027."""
    from app.models import Base

    expected_foreign_keys = {
        "fk_purchase_orders_tenant_supplier",
        "fk_invoices_tenant_supplier",
        "fk_invoices_tenant_purchase_order",
        "fk_invoices_tenant_creator",
        "fk_invoices_tenant_approval_policy",
        "fk_approval_policies_tenant_creator",
        "fk_invoice_approvals_tenant_invoice",
        "fk_invoice_approvals_tenant_approver",
        "fk_invoice_approvals_tenant_policy",
        "fk_auth_sessions_tenant_user",
        "fk_password_reset_tokens_tenant_user",
        "fk_email_verification_tokens_tenant_user",
    }
    metadata_foreign_keys = {
        constraint.name
        for table in Base.metadata.tables.values()
        for constraint in table.foreign_key_constraints
        if constraint.name
    }
    assert expected_foreign_keys <= metadata_foreign_keys

    expected_unique_constraints = {
        "uq_users_tenant_id_id",
        "uq_suppliers_tenant_id_id",
        "uq_purchase_orders_tenant_id_id",
        "uq_approval_policies_tenant_id_id",
        "uq_invoices_tenant_id_id",
    }
    metadata_unique_constraints = {
        constraint.name
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name
    }
    assert expected_unique_constraints <= metadata_unique_constraints


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_composite_tenant_foreign_keys_are_installed_and_validated():
    """The migration must install native PostgreSQL tenant-aware foreign keys."""
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    expected = {
        "fk_purchase_orders_tenant_supplier",
        "fk_invoices_tenant_supplier",
        "fk_invoices_tenant_purchase_order",
        "fk_invoices_tenant_creator",
        "fk_invoices_tenant_approval_policy",
        "fk_approval_policies_tenant_creator",
        "fk_invoice_approvals_tenant_invoice",
        "fk_invoice_approvals_tenant_approver",
        "fk_invoice_approvals_tenant_policy",
        "fk_auth_sessions_tenant_user",
        "fk_password_reset_tokens_tenant_user",
        "fk_email_verification_tokens_tenant_user",
    }
    try:
        with engine.connect() as connection:
            installed = set(connection.execute(text("""
                SELECT conname
                FROM pg_constraint
                WHERE connamespace = 'public'::regnamespace
                  AND contype = 'f'
                  AND convalidated
            """)).scalars())
        assert expected <= installed
    finally:
        engine.dispose()



@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_runtime_role_cannot_attach_another_tenants_user_to_identity_records():
    """Session and recovery records must belong to the referenced user's tenant."""
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    Session = sessionmaker(bind=engine)
    suffix = uuid.uuid4().hex
    tenant_a = "identity-a-" + suffix
    tenant_b = "identity-b-" + suffix
    email_a = f"identity-a-{suffix}@example.invalid"
    email_b = f"identity-b-{suffix}@example.invalid"
    user_a_id = None
    user_b_id = None

    try:
        with engine.begin() as admin:
            user_a_id = admin.execute(text("""
                INSERT INTO public.users
                    (tenant_id, tenant_name, email, hashed_password, role, is_active, email_verified, created_at)
                VALUES (:tenant, 'Test Workspace', :email, 'not-a-real-password-hash', 'analyst', true, true, now())
                RETURNING id
            """), {"tenant": tenant_a, "email": email_a}).scalar_one()
            user_b_id = admin.execute(text("""
                INSERT INTO public.users
                    (tenant_id, tenant_name, email, hashed_password, role, is_active, email_verified, created_at)
                VALUES (:tenant, 'Test Workspace', :email, 'not-a-real-password-hash', 'analyst', true, true, now())
                RETURNING id
            """), {"tenant": tenant_b, "email": email_b}).scalar_one()

        invalid_inserts = (
            (
                "auth_sessions",
                """
                INSERT INTO public.auth_sessions
                    (tenant_id, user_id, token_id, token_hash, family_id, expires_at, created_at)
                VALUES (:tenant, :user_id, :token_id, :token_hash, :family_id, now() + interval '1 hour', now())
                """,
            ),
            (
                "password_reset_tokens",
                """
                INSERT INTO public.password_reset_tokens
                    (tenant_id, user_id, token_hash, expires_at, created_at)
                VALUES (:tenant, :user_id, :token_hash, now() + interval '1 hour', now())
                """,
            ),
            (
                "email_verification_tokens",
                """
                INSERT INTO public.email_verification_tokens
                    (tenant_id, user_id, token_hash, expires_at, created_at)
                VALUES (:tenant, :user_id, :token_hash, now() + interval '1 hour', now())
                """,
            ),
        )

        for table_name, statement in invalid_inserts:
            values = {
                "tenant": tenant_a,
                "user_id": user_b_id,
                "token_id": str(uuid.uuid4()),
                "token_hash": hashlib.sha256(f"{table_name}-{suffix}".encode()).hexdigest(),
                "family_id": str(uuid.uuid4()),
            }
            with Session() as runtime:
                runtime.execute(text("SET LOCAL ROLE ledgergate_runtime"))
                runtime.execute(
                    text("SELECT set_config('app.tenant_id', :tenant, true)"),
                    {"tenant": tenant_a},
                )
                with pytest.raises(Exception, match="cross-tenant or missing identity user reference"):
                    runtime.execute(text(statement), values)
                runtime.rollback()

        # A correctly scoped session remains permitted.
        with Session() as runtime:
            runtime.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            runtime.execute(
                text("SELECT set_config('app.tenant_id', :tenant, true)"),
                {"tenant": tenant_a},
            )
            runtime.execute(text("""
                INSERT INTO public.auth_sessions
                    (tenant_id, user_id, token_id, token_hash, family_id, expires_at, created_at)
                VALUES (:tenant, :user_id, :token_id, :token_hash, :family_id, now() + interval '1 hour', now())
            """), {
                "tenant": tenant_a,
                "user_id": user_a_id,
                "token_id": str(uuid.uuid4()),
                "token_hash": hashlib.sha256(f"valid-session-{suffix}".encode()).hexdigest(),
                "family_id": str(uuid.uuid4()),
            })
            runtime.commit()
    finally:
        with engine.begin() as admin:
            if user_a_id is not None or user_b_id is not None:
                ids = [value for value in (user_a_id, user_b_id) if value is not None]
                for table_name in ("auth_sessions", "password_reset_tokens", "email_verification_tokens"):
                    admin.execute(
                        text(f"DELETE FROM public.{table_name} WHERE user_id = ANY(:ids)"),
                        {"ids": ids},
                    )
                admin.execute(
                    text("DELETE FROM public.users WHERE id = ANY(:ids)"),
                    {"ids": ids},
                )
        engine.dispose()



@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_runtime_role_cannot_attach_cross_tenant_invoice_and_approval_references():
    """Invoice and approval foreign keys must stay inside the row's tenant."""
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    Session = sessionmaker(bind=engine)
    suffix = uuid.uuid4().hex
    tenant_a = "invoice-reference-a-" + suffix
    tenant_b = "invoice-reference-b-" + suffix
    user_ids = {}
    supplier_ids = {}
    po_ids = {}
    policy_ids = {}
    invoice_ids = {}

    try:
        with engine.begin() as admin:
            for key, tenant in (("a", tenant_a), ("b", tenant_b)):
                user_ids[key] = admin.execute(text("""
                    INSERT INTO public.users
                        (tenant_id, tenant_name, email, hashed_password, role,
                         is_active, email_verified, created_at)
                    VALUES (:tenant, 'Reference Test', :email,
                            'not-a-real-password-hash', 'analyst', true, true, now())
                    RETURNING id
                """), {"tenant": tenant, "email": f"invoice-ref-{key}-{suffix}@example.invalid"}).scalar_one()

                supplier_ids[key] = admin.execute(text("""
                    INSERT INTO public.suppliers (tenant_id, name, tax_id, created_at)
                    VALUES (:tenant, :name, NULL, now())
                    RETURNING id
                """), {"tenant": tenant, "name": f"invoice-ref-{key}-{suffix}"}).scalar_one()

                po_ids[key] = admin.execute(text("""
                    INSERT INTO public.purchase_orders
                        (tenant_id, po_number, supplier_id, currency, total_amount, description, created_at)
                    VALUES (:tenant, :number, :supplier_id, 'INR', 100.00, NULL, now())
                    RETURNING id
                """), {
                    "tenant": tenant, "number": f"PO-{key}-{suffix}",
                    "supplier_id": supplier_ids[key],
                }).scalar_one()

                policy_ids[key] = admin.execute(text("""
                    INSERT INTO public.approval_policies
                        (tenant_id, name, min_amount, max_amount, approval_steps,
                         is_active, created_by_user_id, created_at)
                    VALUES (:tenant, :name, 0, NULL, '[]'::json, true, :user_id, now())
                    RETURNING id
                """), {
                    "tenant": tenant, "name": f"Policy-{key}-{suffix}",
                    "user_id": user_ids[key],
                }).scalar_one()

                invoice_ids[key] = admin.execute(text("""
                    INSERT INTO public.invoices
                        (tenant_id, created_by_user_id, invoice_number, supplier_id,
                         purchase_order_id, currency, amount, invoice_date, status,
                         extraction_fields, reconciliation, created_at, approval_policy_id)
                    VALUES (:tenant, :user_id, :number, :supplier_id,
                            :po_id, 'INR', 100.00, '2026-01-01', 'received',
                            '{}'::json, '{}'::json, now(), :policy_id)
                    RETURNING id
                """), {
                    "tenant": tenant, "user_id": user_ids[key],
                    "number": f"INV-{key}-{suffix}", "supplier_id": supplier_ids[key],
                    "po_id": po_ids[key], "policy_id": policy_ids[key],
                }).scalar_one()

        invalid_invoice_references = (
            (supplier_ids["b"], user_ids["a"], po_ids["a"], policy_ids["a"]),
            (supplier_ids["a"], user_ids["a"], po_ids["b"], policy_ids["a"]),
            (supplier_ids["a"], user_ids["b"], po_ids["a"], policy_ids["a"]),
            (supplier_ids["a"], user_ids["a"], po_ids["a"], policy_ids["b"]),
        )
        for index, (supplier_id, creator_id, po_id, policy_id) in enumerate(invalid_invoice_references):
            with Session() as runtime:
                runtime.execute(text("SET LOCAL ROLE ledgergate_runtime"))
                runtime.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": tenant_a})
                with pytest.raises(Exception, match="cross-tenant or missing"):
                    runtime.execute(text("""
                        INSERT INTO public.invoices
                            (tenant_id, created_by_user_id, invoice_number, supplier_id,
                             purchase_order_id, currency, amount, invoice_date, status,
                             extraction_fields, reconciliation, created_at, approval_policy_id)
                        VALUES (:tenant, :user_id, :number, :supplier_id,
                                :po_id, 'INR', 100.00, '2026-01-01', 'received',
                                '{}'::json, '{}'::json, now(), :policy_id)
                    """), {
                        "tenant": tenant_a, "user_id": creator_id,
                        "number": f"INVALID-{index}-{suffix}", "supplier_id": supplier_id,
                        "po_id": po_id, "policy_id": policy_id,
                    })
                runtime.rollback()

        invalid_approval_references = (
            (invoice_ids["b"], user_ids["a"], policy_ids["a"]),
            (invoice_ids["a"], user_ids["b"], policy_ids["a"]),
            (invoice_ids["a"], user_ids["a"], policy_ids["b"]),
        )
        for invoice_id, approver_id, policy_id in invalid_approval_references:
            with Session() as runtime:
                runtime.execute(text("SET LOCAL ROLE ledgergate_runtime"))
                runtime.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": tenant_a})
                with pytest.raises(Exception, match="cross-tenant or missing"):
                    runtime.execute(text("""
                        INSERT INTO public.invoice_approvals
                            (tenant_id, invoice_id, policy_id, stage, approver_user_id,
                             decision, comment, created_at)
                        VALUES (:tenant, :invoice_id, :policy_id, 1, :approver_id,
                                'approved', NULL, now())
                    """), {
                        "tenant": tenant_a, "invoice_id": invoice_id,
                        "policy_id": policy_id, "approver_id": approver_id,
                    })
                runtime.rollback()

        with Session() as runtime:
            runtime.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            runtime.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": tenant_a})
            runtime.execute(text("""
                INSERT INTO public.invoice_approvals
                    (tenant_id, invoice_id, policy_id, stage, approver_user_id,
                     decision, comment, created_at)
                VALUES (:tenant, :invoice_id, :policy_id, 1, :approver_id,
                        'approved', NULL, now())
            """), {
                "tenant": tenant_a, "invoice_id": invoice_ids["a"],
                "policy_id": policy_ids["a"], "approver_id": user_ids["a"],
            })
            runtime.commit()
    finally:
        with engine.begin() as admin:
            if invoice_ids:
                admin.execute(text("DELETE FROM public.invoice_approvals WHERE invoice_id = ANY(:ids)"),
                              {"ids": list(invoice_ids.values())})
                admin.execute(text("DELETE FROM public.invoices WHERE id = ANY(:ids)"),
                              {"ids": list(invoice_ids.values())})
            if po_ids:
                admin.execute(text("DELETE FROM public.purchase_orders WHERE id = ANY(:ids)"),
                              {"ids": list(po_ids.values())})
            if policy_ids:
                admin.execute(text("DELETE FROM public.approval_policies WHERE id = ANY(:ids)"),
                              {"ids": list(policy_ids.values())})
            if supplier_ids:
                admin.execute(text("DELETE FROM public.suppliers WHERE id = ANY(:ids)"),
                              {"ids": list(supplier_ids.values())})
            if user_ids:
                admin.execute(text("DELETE FROM public.users WHERE id = ANY(:ids)"),
                              {"ids": list(user_ids.values())})
        engine.dispose()



@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_runtime_role_cannot_reassign_referenced_user_to_another_tenant():
    """A user with tenant-owned auth sessions cannot be moved to another tenant."""
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    Session = sessionmaker(bind=engine)
    suffix = uuid.uuid4().hex
    tenant_a = "user-reassign-a-" + suffix
    tenant_b = "user-reassign-b-" + suffix
    user_id = None
    session_id = str(uuid.uuid4())
    family_id = str(uuid.uuid4())
    try:
        with engine.begin() as admin:
            user_id = admin.execute(text("""
                INSERT INTO public.users
                    (tenant_id, tenant_name, email, hashed_password, role, is_active, email_verified, created_at)
                VALUES (:tenant, 'Test Workspace', :email, 'not-a-real-password-hash', 'analyst', true, true, now())
                RETURNING id
            """), {"tenant": tenant_a, "email": f"user-reassign-{suffix}@example.invalid"}).scalar_one()
            admin.execute(text("""
                INSERT INTO public.auth_sessions
                    (tenant_id, user_id, token_id, token_hash, family_id, expires_at, created_at)
                VALUES (:tenant, :user_id, :token_id, :token_hash, :family_id, now() + interval '1 day', now())
            """), {
                "tenant": tenant_a,
                "user_id": user_id,
                "token_id": session_id,
                "token_hash": uuid.uuid4().hex + uuid.uuid4().hex,
                "family_id": family_id,
            })

        with Session() as runtime:
            runtime.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            runtime.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": tenant_a})
            with pytest.raises(Exception, match="cannot reassign user tenant while tenant-owned references exist"):
                runtime.execute(text("""
                    UPDATE public.users SET tenant_id = :new_tenant WHERE id = :user_id
                """), {"new_tenant": tenant_b, "user_id": user_id})
            runtime.rollback()

        with engine.connect() as admin:
            owner = admin.execute(text("SELECT tenant_id FROM public.users WHERE id = :id"),
                                  {"id": user_id}).scalar_one()
            assert owner == tenant_a
    finally:
        with engine.begin() as admin:
            if user_id is not None:
                admin.execute(text("DELETE FROM public.auth_sessions WHERE user_id = :id"), {"id": user_id})
                admin.execute(text("DELETE FROM public.users WHERE id = :id"), {"id": user_id})
        engine.dispose()



@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_runtime_role_cannot_reassign_referenced_purchase_order_to_another_tenant():
    """A purchase order referenced by an invoice cannot change tenant."""
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    Session = sessionmaker(bind=engine)
    suffix = uuid.uuid4().hex
    tenant_a = "po-reassign-a-" + suffix
    tenant_b = "po-reassign-b-" + suffix
    supplier_id = None
    po_id = None
    invoice_id = None
    try:
        with engine.begin() as admin:
            supplier_id = admin.execute(text("""
                INSERT INTO public.suppliers (tenant_id, name, tax_id, created_at)
                VALUES (:tenant, :name, NULL, now())
                RETURNING id
            """), {"tenant": tenant_a, "name": "po-reassign-" + suffix}).scalar_one()
            po_id = admin.execute(text("""
                INSERT INTO public.purchase_orders
                    (tenant_id, po_number, supplier_id, currency, total_amount, description, created_at)
                VALUES (:tenant, :number, :supplier_id, 'INR', 100.00, NULL, now())
                RETURNING id
            """), {"tenant": tenant_a, "number": "PO-reassign-" + suffix, "supplier_id": supplier_id}).scalar_one()
            invoice_id = admin.execute(text("""
                INSERT INTO public.invoices
                    (tenant_id, invoice_number, purchase_order_id, currency, amount, invoice_date,
                     status, extraction_fields, reconciliation, created_at)
                VALUES (:tenant, :number, :po_id, 'INR', 100.00, '2026-01-01',
                        'received', '{}'::json, '{}'::json, now())
                RETURNING id
            """), {"tenant": tenant_a, "number": "INV-reassign-" + suffix, "po_id": po_id}).scalar_one()

        with Session() as runtime:
            runtime.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            runtime.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": tenant_a})
            with pytest.raises(Exception, match="cannot reassign purchase-order tenant while tenant-owned references exist"):
                runtime.execute(text("""
                    UPDATE public.purchase_orders SET tenant_id = :new_tenant WHERE id = :po_id
                """), {"new_tenant": tenant_b, "po_id": po_id})
            runtime.rollback()

        with engine.connect() as admin:
            owner = admin.execute(text("SELECT tenant_id FROM public.purchase_orders WHERE id = :id"),
                                  {"id": po_id}).scalar_one()
            assert owner == tenant_a
    finally:
        with engine.begin() as admin:
            if invoice_id is not None:
                admin.execute(text("DELETE FROM public.invoices WHERE id = :id"), {"id": invoice_id})
            if po_id is not None:
                admin.execute(text("DELETE FROM public.purchase_orders WHERE id = :id"), {"id": po_id})
            if supplier_id is not None:
                admin.execute(text("DELETE FROM public.suppliers WHERE id = :id"), {"id": supplier_id})
        engine.dispose()



@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_runtime_role_cannot_reassign_referenced_approval_policy_to_another_tenant():
    """An approval policy referenced by an invoice cannot change tenant."""
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    Session = sessionmaker(bind=engine)
    suffix = uuid.uuid4().hex
    tenant_a = "policy-reassign-a-" + suffix
    tenant_b = "policy-reassign-b-" + suffix
    policy_id = None
    invoice_id = None
    try:
        with engine.begin() as admin:
            policy_id = admin.execute(text("""
                INSERT INTO public.approval_policies
                    (tenant_id, name, min_amount, max_amount, approval_steps,
                     is_active, created_by_user_id, created_at)
                VALUES (:tenant, :name, 0, NULL, '[]'::json, true, NULL, now())
                RETURNING id
            """), {"tenant": tenant_a, "name": "policy-reassign-" + suffix}).scalar_one()
            invoice_id = admin.execute(text("""
                INSERT INTO public.invoices
                    (tenant_id, invoice_number, currency, amount, invoice_date,
                     status, extraction_fields, reconciliation, created_at, approval_policy_id)
                VALUES (:tenant, :number, 'INR', 100.00, '2026-01-01',
                        'received', '{}'::json, '{}'::json, now(), :policy_id)
                RETURNING id
            """), {"tenant": tenant_a, "number": "INV-policy-reassign-" + suffix,
                   "policy_id": policy_id}).scalar_one()

        with Session() as runtime:
            runtime.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            runtime.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": tenant_a})
            with pytest.raises(Exception, match="cannot reassign approval-policy tenant while tenant-owned references exist"):
                runtime.execute(text("""
                    UPDATE public.approval_policies SET tenant_id = :new_tenant WHERE id = :policy_id
                """), {"new_tenant": tenant_b, "policy_id": policy_id})
            runtime.rollback()

        with engine.connect() as admin:
            owner = admin.execute(text("SELECT tenant_id FROM public.approval_policies WHERE id = :id"),
                                  {"id": policy_id}).scalar_one()
            assert owner == tenant_a
    finally:
        with engine.begin() as admin:
            if invoice_id is not None:
                admin.execute(text("DELETE FROM public.invoices WHERE id = :id"), {"id": invoice_id})
            if policy_id is not None:
                admin.execute(text("DELETE FROM public.approval_policies WHERE id = :id"), {"id": policy_id})
        engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_runtime_role_cannot_reassign_referenced_supplier_to_another_tenant():
    """Changing a parent tenant must not invalidate existing child references."""
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    Session = sessionmaker(bind=engine)
    suffix = uuid.uuid4().hex
    tenant_a = "reassign-a-" + suffix
    tenant_b = "reassign-b-" + suffix
    supplier_id = None
    try:
        with engine.begin() as admin:
            supplier_id = admin.execute(text("""
                INSERT INTO public.suppliers (tenant_id, name, tax_id, created_at)
                VALUES (:tenant, :name, NULL, now())
                RETURNING id
            """), {"tenant": tenant_a, "name": "reassign-" + suffix}).scalar_one()
            admin.execute(text("""
                INSERT INTO public.purchase_orders
                    (tenant_id, po_number, supplier_id, currency, total_amount, description, created_at)
                VALUES (:tenant, :number, :supplier_id, 'INR', 10.00, NULL, now())
            """), {"tenant": tenant_a, "number": "reassign-po-" + suffix, "supplier_id": supplier_id})

        with Session() as runtime:
            runtime.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            runtime.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": tenant_a})
            with pytest.raises(Exception, match="cannot reassign supplier tenant while tenant-owned references exist"):
                runtime.execute(text("""
                    UPDATE public.suppliers SET tenant_id = :new_tenant WHERE id = :supplier_id
                """), {"new_tenant": tenant_b, "supplier_id": supplier_id})
            runtime.rollback()

        # The original tenant relationship remains intact after rejection.
        with engine.connect() as admin:
            owner = admin.execute(text("SELECT tenant_id FROM public.suppliers WHERE id = :id"),
                                  {"id": supplier_id}).scalar_one()
            assert owner == tenant_a
    finally:
        with engine.begin() as admin:
            if supplier_id is not None:
                admin.execute(text("DELETE FROM public.purchase_orders WHERE supplier_id = :id"), {"id": supplier_id})
                admin.execute(text("DELETE FROM public.suppliers WHERE id = :id"), {"id": supplier_id})
        engine.dispose()



@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_runtime_role_cannot_reassign_referenced_invoice_to_another_tenant():
    """An invoice with approval history cannot change tenant."""
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    Session = sessionmaker(bind=engine)
    suffix = uuid.uuid4().hex
    tenant_a = "invoice-reassign-a-" + suffix
    tenant_b = "invoice-reassign-b-" + suffix
    user_id = None
    invoice_id = None
    try:
        with engine.begin() as admin:
            user_id = admin.execute(text("""
                INSERT INTO public.users
                    (tenant_id, tenant_name, email, hashed_password, role,
                     is_active, email_verified, created_at)
                VALUES (:tenant, 'Invoice Reassign Test', :email,
                        'not-a-real-password-hash', 'analyst', true, true, now())
                RETURNING id
            """), {
                "tenant": tenant_a,
                "email": f"invoice-reassign-{suffix}@example.invalid",
            }).scalar_one()
            invoice_id = admin.execute(text("""
                INSERT INTO public.invoices
                    (tenant_id, invoice_number, currency, amount, invoice_date,
                     status, extraction_fields, reconciliation, created_at)
                VALUES (:tenant, :number, 'INR', 100.00, '2026-01-01',
                        'received', '{}'::json, '{}'::json, now())
                RETURNING id
            """), {
                "tenant": tenant_a,
                "number": "INV-reassign-" + suffix,
            }).scalar_one()
            admin.execute(text("""
                INSERT INTO public.invoice_approvals
                    (tenant_id, invoice_id, policy_id, stage, approver_user_id,
                     decision, comment, created_at)
                VALUES (:tenant, :invoice_id, NULL, 1, :approver_id,
                        'pending', NULL, now())
            """), {
                "tenant": tenant_a,
                "invoice_id": invoice_id,
                "approver_id": user_id,
            })

        with Session() as runtime:
            runtime.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            runtime.execute(
                text("SELECT set_config('app.tenant_id', :tenant, true)"),
                {"tenant": tenant_a},
            )
            with pytest.raises(
                Exception,
                match="cannot reassign invoice tenant while tenant-owned references exist",
            ):
                runtime.execute(text("""
                    UPDATE public.invoices SET tenant_id = :new_tenant WHERE id = :invoice_id
                """), {"new_tenant": tenant_b, "invoice_id": invoice_id})
            runtime.rollback()

        with engine.connect() as admin:
            owner = admin.execute(
                text("SELECT tenant_id FROM public.invoices WHERE id = :id"),
                {"id": invoice_id},
            ).scalar_one()
            assert owner == tenant_a
    finally:
        with engine.begin() as admin:
            if invoice_id is not None:
                admin.execute(
                    text("DELETE FROM public.invoice_approvals WHERE invoice_id = :id"),
                    {"id": invoice_id},
                )
                admin.execute(
                    text("DELETE FROM public.invoices WHERE id = :id"),
                    {"id": invoice_id},
                )
            if user_id is not None:
                admin.execute(
                    text("DELETE FROM public.users WHERE id = :id"),
                    {"id": user_id},
                )
        engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="Requires disposable PostgreSQL database")
def test_postgres_has_validated_tenant_composite_constraints():
    """Verify migration 0027 installed real, validated PostgreSQL constraints."""
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    expected_foreign_keys = {
        "fk_purchase_orders_tenant_supplier",
        "fk_invoices_tenant_supplier",
        "fk_invoices_tenant_purchase_order",
        "fk_invoices_tenant_creator",
        "fk_invoices_tenant_approval_policy",
        "fk_approval_policies_tenant_creator",
        "fk_invoice_approvals_tenant_invoice",
        "fk_invoice_approvals_tenant_approver",
        "fk_invoice_approvals_tenant_policy",
        "fk_auth_sessions_tenant_user",
        "fk_password_reset_tokens_tenant_user",
        "fk_email_verification_tokens_tenant_user",
    }
    expected_unique_constraints = {
        "uq_users_tenant_id_id",
        "uq_suppliers_tenant_id_id",
        "uq_purchase_orders_tenant_id_id",
        "uq_approval_policies_tenant_id_id",
        "uq_invoices_tenant_id_id",
    }
    expected = expected_foreign_keys | expected_unique_constraints
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT conname, contype, convalidated
                FROM pg_catalog.pg_constraint
                WHERE conname = ANY(:constraint_names)
            """), {"constraint_names": sorted(expected)}).mappings().all()

        actual = {row["conname"]: row for row in rows}
        assert set(actual) == expected
        assert all(actual[name]["contype"] == "f" for name in expected_foreign_keys)
        assert all(actual[name]["contype"] == "u" for name in expected_unique_constraints)
        assert all(actual[name]["convalidated"] for name in expected)
    finally:
        engine.dispose()
