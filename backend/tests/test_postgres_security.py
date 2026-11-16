import os
import uuid
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
              (tenant_id, actor_user_id, entity_type, entity_id, action, details, created_at)
            VALUES
              ('tenant-a', NULL, 'test', :event_id, 'created', '{}'::json, now())
        """), {"event_id": event_id})
        db.commit()

    with Session() as db:
        db.execute(text("SET LOCAL ROLE ledgergate_runtime"))
        db.execute(text("SELECT set_config('app.tenant_id', 'tenant-b', false)"))
        visible = db.execute(
            text("SELECT count(*) FROM audit_events WHERE entity_id=:event_id"),
            {"event_id": event_id},
        ).scalar_one()
        assert visible == 0
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
