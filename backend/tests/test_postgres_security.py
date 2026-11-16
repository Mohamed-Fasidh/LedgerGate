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
