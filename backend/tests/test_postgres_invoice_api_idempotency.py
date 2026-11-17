"""Concurrent authenticated invoice decisions must replay one durable result."""

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="Requires disposable PostgreSQL database",
)
@pytest.mark.parametrize(("decision", "expected_status", "audit_action"), [("approve", "approved", "human_approved"), ("reject", "rejected", "human_rejected")])
def test_concurrent_invoice_decision_http_requests_replay_same_idempotent_response(monkeypatch, decision, expected_status, audit_action):
    """Exercise real approval/rejection APIs, auth, PostgreSQL locks, and replay."""
    from app import main
    from app.audit import verify_tenant_chain
    from app.models import (
        AuditEvent,
        IdempotencyRecord,
        Invoice,
        InvoiceApproval,
        InvoiceStatus,
        Role,
        User,
    )
    from app.security import create_access_token

    engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_size=5, max_overflow=0)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    tenant_id = "invoice-api-race-" + uuid.uuid4().hex
    suffix = uuid.uuid4().hex
    creator = User(
        tenant_id=tenant_id,
        tenant_name="Concurrent API test",
        email=f"maker-{suffix}@example.test",
        hashed_password="unused-test-hash",
        role=Role.analyst,
        is_active=True,
        email_verified=True,
    )
    approver = User(
        tenant_id=tenant_id,
        tenant_name="Concurrent API test",
        email=f"reviewer-{suffix}@example.test",
        hashed_password="unused-test-hash",
        role=Role.reviewer,
        is_active=True,
        email_verified=True,
    )
    invoice = Invoice(
        tenant_id=tenant_id,
        invoice_number="RACE-" + suffix[:16],
        currency="INR",
        amount=100,
        status=InvoiceStatus.review_required,
        reconciliation={"decision": "MATCHED"},
        approval_steps_snapshot=[["reviewer"]],
        approval_stage=1,
    )
    request_key = decision + "-race-" + suffix
    invoice_id = None
    access_token = None
    original_override = main.app.dependency_overrides.get(main.get_db)
    barrier = threading.Barrier(2)

    def runtime_database_dependency():
        db = Session()
        try:
            db.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            yield db
        finally:
            try:
                db.rollback()
                db.execute(text("RESET app.tenant_id"))
                db.commit()
            except Exception:
                db.rollback()
            db.close()

    try:
        # Pause the winner after it acquires the invoice lock, so the second
        # authenticated request can be observed waiting on PostgreSQL's lock.
        with engine.begin() as connection:
            connection.exec_driver_sql(
                f'CREATE FUNCTION public."{function_name}"() RETURNS trigger '
                f'LANGUAGE plpgsql AS $ BEGIN '
                f"IF NEW.tenant_id = '{tenant_id}' THEN PERFORM pg_sleep(1.5); END IF; "
                f'RETURN NEW; END $'
            )
            connection.exec_driver_sql(
                f'CREATE TRIGGER "{trigger_name}" BEFORE INSERT ON public.invoice_approvals '
                f'FOR EACH ROW EXECUTE FUNCTION public."{function_name}"()'
            )

        with Session.begin() as db:
            db.add_all([creator, approver])
            db.flush()
            invoice.created_by_user_id = creator.id
            db.add(invoice)
            db.flush()
            invoice_id = invoice.id
            access_token = create_access_token(approver)

        monkeypatch.setitem(
            main.app.dependency_overrides, main.get_db, runtime_database_dependency
        )

        def submit_approval():
            with TestClient(main.app) as client:
                barrier.wait(timeout=10)
                return client.post(
                    f"/api/v1/invoices/{invoice_id}/{decision}",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Idempotency-Key": request_key,
                    },
                    json={"comment": "Concurrent duplicate approval"},
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(submit_approval)
            second = executor.submit(submit_approval)
            responses = [first.result(timeout=30), second.result(timeout=30)]

        assert [response.status_code for response in responses] == [200, 200]
        response_bodies = [response.json() for response in responses]
        assert response_bodies[0] == response_bodies[1]
        assert response_bodies[0]["id"] == invoice_id
        assert response_bodies[0]["status"] == expected_status

        with Session.begin() as db:
            db.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            approvals = db.query(InvoiceApproval).filter(
                InvoiceApproval.tenant_id == tenant_id,
                InvoiceApproval.invoice_id == invoice_id,
            ).all()
            records = db.query(IdempotencyRecord).filter(
                IdempotencyRecord.tenant_id == tenant_id,
                IdempotencyRecord.key == request_key,
            ).all()
            stored_invoice = db.query(Invoice).filter(
                Invoice.tenant_id == tenant_id,
                Invoice.id == invoice_id,
            ).one()
            audit_events = db.query(AuditEvent).filter(
                AuditEvent.tenant_id == tenant_id,
                AuditEvent.entity_type == "invoice",
                AuditEvent.entity_id == str(invoice_id),
                AuditEvent.action == audit_action,
            ).all()

        assert len(approvals) == 1
        assert approvals[0].decision == expected_status
        assert approvals[0].approver_user_id == approver.id
        assert len(records) == 1
        assert records[0].response_json == response_bodies[0]
        assert stored_invoice.status == InvoiceStatus(expected_status)
        assert len(audit_events) == 1

        with Session() as db:
            verification = verify_tenant_chain(db, tenant_id)
        assert verification["valid"] is True
        assert verification["verified_events"] >= 1
    finally:
        if original_override is None:
            main.app.dependency_overrides.pop(main.get_db, None)
        else:
            main.app.dependency_overrides[main.get_db] = original_override
        try:
            with Session.begin() as db:
                if invoice_id is not None:
                    db.query(IdempotencyRecord).filter(
                        IdempotencyRecord.tenant_id == tenant_id
                    ).delete(synchronize_session=False)
                    db.query(InvoiceApproval).filter(
                        InvoiceApproval.tenant_id == tenant_id
                    ).delete(synchronize_session=False)
                    db.query(Invoice).filter(
                        Invoice.tenant_id == tenant_id
                    ).delete(synchronize_session=False)
                db.query(User).filter(
                    User.tenant_id == tenant_id
                ).delete(synchronize_session=False)
        finally:
            engine.dispose()
