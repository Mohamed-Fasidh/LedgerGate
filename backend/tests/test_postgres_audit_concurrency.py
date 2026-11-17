"""Concurrent PostgreSQL audit appends must remain one valid tenant hash chain."""

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="Requires disposable PostgreSQL database",
)
def test_concurrent_audit_writers_append_to_one_unbroken_tenant_chain():
    from app.audit import record_event, verify_tenant_chain
    from app.models import AuditEvent

    application_name = "ledgergate-audit-" + uuid.uuid4().hex[:16]
    engine = create_engine(
        os.environ["TEST_POSTGRES_URL"],
        pool_size=3,
        max_overflow=0,
        connect_args={"application_name": application_name},
    )
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    tenant_id = "audit-race-" + uuid.uuid4().hex
    actor = SimpleNamespace(id=1, tenant_id=tenant_id)
    second_started = threading.Event()

    def append_second_event():
        second_started.set()
        with Session.begin() as db:
            db.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            db.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": tenant_id})
            event = record_event(
                db,
                actor,
                "concurrency_test",
                f"{tenant_id}-second",
                "concurrent_append",
                {"writer": "second"},
            )
            return event.event_hash

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with Session.begin() as first:
                first.execute(text("SET LOCAL ROLE ledgergate_runtime"))
                first.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": tenant_id})
                first_event = record_event(
                    first,
                    actor,
                    "concurrency_test",
                    f"{tenant_id}-first",
                    "concurrent_append",
                    {"writer": "first"},
                )
                first_hash = first_event.event_hash
                pending = executor.submit(append_second_event)
                assert second_started.wait(timeout=5), "second writer did not start"

                # Do not rely on timing alone: observe the competing backend
                # blocked specifically on the tenant's PostgreSQL advisory lock.
                deadline = time.monotonic() + 10
                second_waiting_for_advisory_lock = False
                while time.monotonic() < deadline:
                    with engine.connect() as observer:
                        waiting = observer.execute(
                            text("""
                                SELECT count(*)
                                FROM pg_stat_activity
                                WHERE application_name = :application_name
                                  AND wait_event_type = 'Lock'
                                  AND wait_event = 'advisory'
                            """),
                            {"application_name": application_name},
                        ).scalar_one()
                    if waiting:
                        second_waiting_for_advisory_lock = True
                        break
                    time.sleep(0.05)

                assert second_waiting_for_advisory_lock, (
                    "second writer never waited on the PostgreSQL tenant "
                    "advisory lock while the first transaction was open"
                )

            second_hash = pending.result(timeout=10)

        with Session.begin() as db:
            db.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            db.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": tenant_id})
            rows = (
                db.query(AuditEvent)
                .filter(AuditEvent.tenant_id == tenant_id)
                .order_by(AuditEvent.id.asc())
                .all()
            )
            verification = verify_tenant_chain(db, tenant_id)

        assert len(rows) == 2
        assert {row.event_hash for row in rows} == {first_hash, second_hash}
        assert rows[0].previous_hash == "0" * 64
        assert rows[1].previous_hash == rows[0].event_hash
        assert verification["valid"] is True
        assert verification["verified_events"] == 2
    finally:
        # Audit rows are append-only by design. The unique tenant prefix keeps
        # this test's two immutable records isolated in the disposable CI DB.
        engine.dispose()
