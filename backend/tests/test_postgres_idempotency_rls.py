"""Runtime-role RLS must isolate shared idempotency keys by tenant."""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="Requires disposable PostgreSQL database",
)
def test_runtime_role_cannot_read_another_tenants_idempotency_response():
    from app.main import _get_idempotent_response
    from app.models import IdempotencyRecord

    engine = create_engine(os.environ["TEST_POSTGRES_URL"], pool_size=2, max_overflow=0)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    tenant_a = "idem-rls-a-" + suffix
    tenant_b = "idem-rls-b-" + suffix
    shared_key = "rls-shared-key-" + suffix[:12]
    hash_a = "c" * 64
    hash_b = "d" * 64
    response_a = {"tenant": "A", "resource_id": 303}
    response_b = {"tenant": "B", "resource_id": 404}

    try:
        # Seed both tenant records with the migration/test database owner.
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

        # Exercise the same role and transaction-local tenant context used by
        # the API, not a superuser query that bypasses row-level security.
        with Session.begin() as db:
            db.execute(text("SET LOCAL ROLE ledgergate_runtime"))
            db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_a},
            )
            visible_a = db.query(IdempotencyRecord).filter(
                IdempotencyRecord.key == shared_key
            ).all()
            assert [(row.tenant_id, row.response_json) for row in visible_a] == [
                (tenant_a, response_a)
            ]
            assert _get_idempotent_response(
                db, tenant_a, shared_key, "supplier.create", hash_a
            ) == response_a
            assert _get_idempotent_response(
                db, tenant_b, shared_key, "invoice.create", hash_b
            ) is None

            db.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_b},
            )
            visible_b = db.query(IdempotencyRecord).filter(
                IdempotencyRecord.key == shared_key
            ).all()
            assert [(row.tenant_id, row.response_json) for row in visible_b] == [
                (tenant_b, response_b)
            ]
            assert _get_idempotent_response(
                db, tenant_b, shared_key, "invoice.create", hash_b
            ) == response_b
    finally:
        with Session.begin() as db:
            db.query(IdempotencyRecord).filter(
                IdempotencyRecord.tenant_id.in_([tenant_a, tenant_b])
            ).delete(synchronize_session=False)
        engine.dispose()
