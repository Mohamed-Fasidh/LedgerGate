import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.database import SessionLocal, set_tenant_context


@pytest.mark.skipif(
    not os.getenv("RLS_TEST_ADMIN_DATABASE_URL"),
    reason="PostgreSQL RLS integration test requires RLS_TEST_ADMIN_DATABASE_URL",
)
def test_postgres_rls_isolates_tenants_and_reapplies_context_after_commit():
    admin_engine = create_engine(os.environ["RLS_TEST_ADMIN_DATABASE_URL"])
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    name_a = f"rls-a-{tenant_a}"
    name_b = f"rls-b-{tenant_b}"

    try:
        # Seed both tenants through the privileged migration/admin connection.
        with admin_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO suppliers (tenant_id, name) "
                    "VALUES (:tenant_a, :name_a), (:tenant_b, :name_b)"
                ),
                {
                    "tenant_a": tenant_a,
                    "name_a": name_a,
                    "tenant_b": tenant_b,
                    "name_b": name_b,
                },
            )

        with SessionLocal() as db:
            # No tenant context must expose no tenant-owned rows.
            assert db.execute(
                text("SELECT name FROM suppliers WHERE name IN (:a, :b)"),
                {"a": name_a, "b": name_b},
            ).scalars().all() == []
            db.rollback()

            set_tenant_context(db, tenant_a)
            visible = db.execute(
                text("SELECT name FROM suppliers WHERE name IN (:a, :b)"),
                {"a": name_a, "b": name_b},
            ).scalars().all()
            assert visible == [name_a]

            # The context must be re-established after a transaction boundary.
            db.commit()
            visible_after_commit = db.execute(
                text("SELECT name FROM suppliers WHERE name IN (:a, :b)"),
                {"a": name_a, "b": name_b},
            ).scalars().all()
            assert visible_after_commit == [name_a]

            with pytest.raises(DBAPIError):
                db.execute(
                    text("INSERT INTO suppliers (tenant_id, name) VALUES (:tenant, :name)"),
                    {"tenant": tenant_b, "name": f"cross-tenant-{uuid.uuid4()}"},
                )
            db.rollback()
    finally:
        with admin_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM suppliers WHERE tenant_id IN (:a, :b)"),
                {"a": tenant_a, "b": tenant_b},
            )
        admin_engine.dispose()
