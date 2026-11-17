"""Real PostgreSQL row-lock behavior for concurrent invoice transitions."""

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="Requires disposable PostgreSQL database",
)
def test_invoice_for_update_blocks_second_transaction_until_first_commits():
    """A second independent DB session must wait on the same tenant invoice row."""
    from app.models import Invoice

    engine = create_engine(
        os.environ["TEST_POSTGRES_URL"],
        pool_size=3,
        max_overflow=0,
    )
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    tenant_id = "invoice-lock-" + uuid.uuid4().hex
    invoice = Invoice(
        tenant_id=tenant_id,
        invoice_number="LOCK-" + uuid.uuid4().hex[:16],
    )
    invoice_id = None
    second_started = threading.Event()
    second_acquired = threading.Event()

    try:
        with Session.begin() as db:
            db.add(invoice)
            db.flush()
            invoice_id = invoice.id

        def acquire_from_second_connection():
            second_started.set()
            with Session.begin() as db:
                locked = (
                    db.query(Invoice)
                    .filter(
                        Invoice.tenant_id == tenant_id,
                        Invoice.id == invoice_id,
                    )
                    .with_for_update()
                    .one()
                )
                assert locked.id == invoice_id
                second_acquired.set()

        with ThreadPoolExecutor(max_workers=1) as executor:
            with Session.begin() as first:
                locked = (
                    first.query(Invoice)
                    .filter(
                        Invoice.tenant_id == tenant_id,
                        Invoice.id == invoice_id,
                    )
                    .with_for_update()
                    .one()
                )
                assert locked.id == invoice_id

                pending = executor.submit(acquire_from_second_connection)
                assert second_started.wait(timeout=5), (
                    "second transaction did not start"
                )
                assert not second_acquired.wait(timeout=0.3), (
                    "second transaction acquired the invoice lock before "
                    "the first transaction committed"
                )

            assert second_acquired.wait(timeout=5), (
                "second transaction did not acquire the row lock after "
                "the first transaction committed"
            )
            pending.result(timeout=5)
    finally:
        if invoice_id is not None:
            with Session.begin() as db:
                db.query(Invoice).filter(
                    Invoice.tenant_id == tenant_id,
                    Invoice.id == invoice_id,
                ).delete(synchronize_session=False)
        engine.dispose()
