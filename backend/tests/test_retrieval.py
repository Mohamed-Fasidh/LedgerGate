import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import PolicyDocument, PurchaseOrder, Supplier
from app.services.retrieval import (
    MAX_RETRIEVAL_RESULTS,
    char_ngrams,
    hybrid_score,
    retrieve_business_records,
    retrieve_policy,
)


@pytest.fixture
def retrieval_db():
    engine = create_engine("sqlite:///:memory:")
    tables = [Supplier.__table__, PurchaseOrder.__table__, PolicyDocument.__table__]
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as db:
        yield db
    engine.dispose()


def test_hybrid_retrieval_prefers_matching_text():
    assert hybrid_score("invoice approval duplicate", "Invoice approval policy for duplicate payments") > hybrid_score("invoice approval duplicate", "robot arm motion planning")


def test_character_ngrams_are_deterministic():
    assert char_ngrams("alpha beta gamma") == char_ngrams("alpha beta gamma")


def test_policy_retrieval_is_tenant_scoped_and_result_bounded(retrieval_db):
    db = retrieval_db
    db.add_all(
        [
            PolicyDocument(tenant_id="tenant-a", title=f"Invoice approval {i}", body="duplicate invoice review")
            for i in range(MAX_RETRIEVAL_RESULTS + 5)
        ]
    )
    db.add(
        PolicyDocument(
            tenant_id="tenant-b",
            title="Invoice approval confidential",
            body="duplicate invoice review",
        )
    )
    db.commit()

    results = retrieve_policy(db, "tenant-a", "invoice approval duplicate", limit=10_000)

    assert len(results) == MAX_RETRIEVAL_RESULTS
    assert all(item["title"].startswith("Invoice approval ") for item in results)
    assert all(item["id"] != 0 for item in results)
    assert retrieve_policy(db, "tenant-a", "   ") == []
    assert retrieve_policy(db, "tenant-a", "invoice approval", limit=-10)
    assert len(retrieve_policy(db, "tenant-a", "invoice approval", limit=-10)) == 1


def test_business_retrieval_scopes_purchase_orders_and_suppliers(retrieval_db):
    db = retrieval_db
    supplier_a = Supplier(tenant_id="tenant-a", name="Acme Supplies")
    supplier_b = Supplier(tenant_id="tenant-b", name="Acme Supplies")
    db.add_all([supplier_a, supplier_b])
    db.flush()
    db.add_all(
        [
            PurchaseOrder(
                tenant_id="tenant-a",
                po_number=f"ACME-INVOICE-{i}",
                supplier_id=supplier_a.id,
                total_amount=100,
                currency="INR",
                description="office equipment",
            )
            for i in range(MAX_RETRIEVAL_RESULTS + 5)
        ]
        + [
            PurchaseOrder(
                tenant_id="tenant-b",
                po_number="ACME-INVOICE-SECRET",
                supplier_id=supplier_b.id,
                total_amount=999,
                currency="INR",
                description="confidential equipment",
            )
        ]
    )
    db.commit()

    results = retrieve_business_records(db, "tenant-a", "acme invoice supplies", limit=10_000)

    assert len(results) == MAX_RETRIEVAL_RESULTS
    assert all(item["reference"].startswith("ACME-INVOICE-") for item in results)
    assert all(item["amount"] == 100.0 for item in results)
    assert all(item["supplier"] == "Acme Supplies" for item in results)
    assert retrieve_business_records(db, "tenant-a", "", limit=10) == []
