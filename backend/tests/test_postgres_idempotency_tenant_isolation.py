"""Idempotency keys are tenant-scoped and cannot cross operation boundaries."""

import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="Requires disposable PostgreSQL database",
)
def test_idempotency_key_is_isolated_by_tenant_and_unique_within_tenant():
    from app.main import _get_idempotent_response
    from app.models import IdempotencyRecord

    engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_size=2, max_overflow=0)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    tenant_a = "idem-a-" + suffix
    tenant_b = "idem-b-" + suffix
    shared_key = "cross-tenant-key-" + suffix[:12]
    hash_a = "a" * 64
    hash_b = "b" * 64
    response_a = {"resource": "tenant-a", "id": 101}
    response_b = {"resource": "tenant-b", "id": 202}

    try:
        with Session.begin() as db:
            db.add_all(
                [
                    IdempotencyRecord(
                        tenant_id=tenant_a,
                        key=shared_key,
                        operation="supplier.create",
                        request_hash=hash_a,
                        response_json=response_a,
                    ),
                    IdempotencyRecord(
                        tenant_id=tenant_b,
                        key=shared_key,
                        operation="invoice.create",
                        request_hash=hash_b,
                        response_json=response_b,
                    ),
                ]
            )
            db.flush()

            assert _get_idempotent_response(
                db, tenant_a, shared_key, "supplier.create", hash_a
            ) == response_a
            assert _get_idempotent_response(
                db, tenant_b, shared_key, "invoice.create", hash_b
            ) == response_b

            # A key owned by tenant A is invisible to tenant B when no
            # tenant-B record exists, rather than replaying tenant A's body.
            assert _get_idempotent_response(
                db, tenant_b, "tenant-a-only-" + suffix[:8],
                "supplier.create", hash_a,
            ) is None

            with pytest.raises(HTTPException) as exc:
                _get_idempotent_response(
                    db, tenant_a, shared_key, "invoice.create", hash_a
                )
            assert exc.value.status_code == 409

        with pytest.raises(IntegrityError):
            with Session.begin() as db:
                db.add(
                    IdempotencyRecord(
                        tenant_id=tenant_a,
                        key=shared_key,
                        operation="supplier.create",
                        request_hash=hash_a,
                        response_json={"duplicate": True},
                    )
                )
                db.flush()
    finally:
        with Session.begin() as db:
            db.query(IdempotencyRecord).filter(
                IdempotencyRecord.tenant_id.in_([tenant_a, tenant_b])
            ).delete(synchronize_session=False)
        engine.dispose()
