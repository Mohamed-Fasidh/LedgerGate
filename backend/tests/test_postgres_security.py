import os
import uuid
import hashlib
import pytest
from sqlalchemy import create_engine, text
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
def test_runtime_role_has_delete_only_for_explicit_cleanup_tables():
    engine = create_engine(os.environ["TEST_POSTGRES_URL"])
    try:
        with engine.connect() as connection:
            denied_tables = (
                "users",
                "suppliers",
                "purchase_orders",
                "invoices",
                "approval_policies",
                "invoice_approvals",
                "audit_events",
                "password_reset_tokens",
            )
            allowed_cleanup_tables = (
                "registration_attempts",
                "login_attempts",
                "email_verification_tokens",
                "auth_sessions",
                "idempotency_records",
                "outbound_emails",
            )
            for table in denied_tables:
                allowed = connection.execute(
                    text("SELECT has_table_privilege('ledgergate_runtime', :table_name, 'DELETE')"),
                    {"table_name": f"public.{table}"},
                ).scalar_one()
                assert allowed is False, f"ledgergate_runtime must not DELETE from {table}"

            for table in allowed_cleanup_tables:
                allowed = connection.execute(
                    text("SELECT has_table_privilege('ledgergate_runtime', :table_name, 'DELETE')"),
                    {"table_name": f"public.{table}"},
                ).scalar_one()
                assert allowed is True, f"ledgergate_runtime needs bounded cleanup DELETE on {table}"

            for privilege in ("UPDATE", "DELETE"):
                allowed = connection.execute(
                    text("SELECT has_table_privilege('ledgergate_runtime', 'public.audit_events', :privilege)"),
                    {"privilege": privilege},
                ).scalar_one()
                assert allowed is False, f"ledgergate_runtime must not {privilege} immutable audit events"
    finally:
        engine.dispose()
