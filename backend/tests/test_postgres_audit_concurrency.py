"""Concurrent PostgreSQL audit appends must remain one valid tenant hash chain."""

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="Requires disposable PostgreSQL database",
)
def test_concurrent_audit_writers_append_to_one_unbroken_tenant_chain():
    from app.audit import verify_tenant_chain, record_event
    from app.models import AuditEvent

    engine = create_engine(
        os.environ["TEST_POSTGRES_URL"],
        pool_size=3,
        max_overflow=0,
    )
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    tenant_id = "audit-race-" + uuid.uuid4().hex
    actor = SimpleNamespace(id=1, tenant_id=tenant_id)
    start = threading.Barrier(3)

    def append_event(index):
        with Session.begin() as db:
            start.wait(timeout=10)
            event = record_event(
                db,
                actor,
                "concurrency_test",
                f"{tenant_id}-{index}",
                "concurrent_append",
                {"writer": index},
            )
            return event.event_hash

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(append_event, 1)
            second = executor.submit(append_event, 2)
            start.wait(timeout=10)
            first_hash = first.result(timeout=15)
            second_hash = second.result(timeout=15)

        with Session.begin() as db:
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
