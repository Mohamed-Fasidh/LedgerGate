"""Conflicting concurrent invoice decisions must serialize to one winner."""

import os
import threading
import time
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
def test_concurrent_approval_and_rejection_cannot_both_commit(monkeypatch):
    """Two conflicting real HTTP decisions must serialize on the invoice row."""
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

    application_name = "ledgergate-invoice-conflict-" + uuid.uuid4().hex[:12]
    engine = create_engine(
        os.environ["TEST_POSTGRES_URL"],
        pool_size=5,
        max_overflow=0,
        connect_args={"application_name": application_name},
    )
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    tenant_id = "invoice-conflict-" + uuid.uuid4().hex
    suffix = uuid.uuid4().hex
    trigger_suffix = uuid.uuid4().hex[:12]
    trigger_name = "invoice_conflict_delay_" + trigger_suffix
    function_name = "invoice_conflict_delay_fn_" + trigger_suffix
    creator = User(
        tenant_id=tenant_id,
        tenant_name="Conflicting decisions test",
        email=f"maker-{suffix}@example.test",
        hashed_password="unused-test-hash",
        role=Role.analyst,
        is_active=True,
        email_verified=True,
    )
    approver = User(
        tenant_id=tenant_id,
        tenant_name="Conflicting decisions test",
        email=f"reviewer-{suffix}@example.test",
        hashed_password="unused-test-hash",
        role=Role.reviewer,
        is_active=True,
        email_verified=True,
    )
    invoice = Invoice(
        tenant_id=tenant_id,
        invoice_number="CONFLICT-" + suffix[:16],
        currency="INR",
        amount=250,
        status=InvoiceStatus.review_required,
        reconciliation={"decision": "MATCHED"},
        approval_steps_snapshot=[["reviewer"]],
        approval_stage=1,
    )
    invoice_id = None
    access_token = None
    approve_key = "conflict-approve-" + suffix
    reject_key = "conflict-reject-" + suffix
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
        with engine.begin() as connection:
            connection.exec_driver_sql(
                f'CREATE FUNCTION public."{function_name}"() RETURNS trigger '
                f'LANGUAGE plpgsql AS $$ BEGIN '
                f"IF NEW.tenant_id = '{tenant_id}' THEN PERFORM pg_sleep(1.5); END IF; "
                f'RETURN NEW; END $$'
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

        def submit_decision(decision, key):
            with TestClient(main.app) as client:
                barrier.wait(timeout=10)
                return client.post(
                    f"/api/v1/invoices/{invoice_id}/{decision}",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Idempotency-Key": key,
                    },
                    json={"comment": f"Concurrent {decision} decision"},
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            approve_future = executor.submit(submit_decision, "approve", approve_key)
            reject_future = executor.submit(submit_decision, "reject", reject_key)

            deadline = time.monotonic() + 8
            observed_lock_wait = False
            while time.monotonic() < deadline and not (
                approve_future.done() and reject_future.done()
            ):
                with engine.connect() as observer:
                    waiting = observer.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE application_name = :application_name "
                            "AND pid <> pg_backend_pid() "
                            "AND wait_event_type = 'Lock' "
                            "AND wait_event = 'transactionid'"
                        ),
                        {"application_name": application_name},
                    ).scalar_one()
                if waiting:
                    observed_lock_wait = True
                    break
                time.sleep(0.025)

            assert observed_lock_wait, (
                "conflicting decision request never waited on the invoice row lock"
            )
            approve_response = approve_future.result(timeout=30)
            reject_response = reject_future.result(timeout=30)

        assert sorted([approve_response.status_code, reject_response.status_code]) == [
            200,
            409,
        ]
        winning_response = (
            approve_response if approve_response.status_code == 200 else reject_response
        )
        winning_body = winning_response.json()
        winning_status = winning_body["status"]
        assert winning_status in {"approved", "rejected"}

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
            ).all()
            stored_invoice = db.query(Invoice).filter(
                Invoice.tenant_id == tenant_id,
                Invoice.id == invoice_id,
            ).one()
            audit_events = db.query(AuditEvent).filter(
                AuditEvent.tenant_id == tenant_id,
                AuditEvent.entity_type == "invoice",
                AuditEvent.entity_id == str(invoice_id),
                AuditEvent.action.in_(["human_approved", "human_rejected"]),
            ).all()

        assert len(approvals) == 1
        assert approvals[0].decision == winning_status
        assert stored_invoice.status == InvoiceStatus(winning_status)
        assert len(records) == 1
        assert len(audit_events) == 1
        assert audit_events[0].action == (
            "human_approved" if winning_status == "approved" else "human_rejected"
        )

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
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    f'DROP TRIGGER IF EXISTS "{trigger_name}" ON public.invoice_approvals'
                )
                connection.exec_driver_sql(
                    f'DROP FUNCTION IF EXISTS public."{function_name}"()'
                )
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
