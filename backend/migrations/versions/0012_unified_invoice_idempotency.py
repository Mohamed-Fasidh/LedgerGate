"""Reserve legacy invoice idempotency keys in the shared tenant namespace."""
from alembic import op
import sqlalchemy as sa

revision = "0012_unified_invoice_idempotency"
down_revision = "0011_idempotency_records"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "idempotency_records" not in sa.inspect(bind).get_table_names():
        raise RuntimeError("idempotency_records must exist before unifying invoice idempotency")

    legacy_rows = bind.execute(sa.text("""
        SELECT tenant_id, id, idempotency_key, idempotency_operation,
               idempotency_request_hash, created_at
        FROM invoices
        WHERE idempotency_key IS NOT NULL
        ORDER BY tenant_id, id
    """)).mappings().all()

    # Fail rather than silently overwrite a key already reserved by another
    # operation. The operator can inspect and resolve any historical collision.
    for row in legacy_rows:
        existing = bind.execute(sa.text("""
            SELECT operation FROM idempotency_records
            WHERE tenant_id = :tenant_id AND key = :key
        """), {"tenant_id": row["tenant_id"], "key": row["idempotency_key"]}).first()
        if existing:
            raise RuntimeError(
                "Cannot unify idempotency namespace: tenant "
                f"{row['tenant_id']} has key {row['idempotency_key']!r} "
                f"already reserved for {existing[0]!r}; resolve the collision before migration."
            )

    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        # The migration owner must be able to backfill tenant rows despite
        # FORCE RLS. RLS remains enabled; owner bypass is restored afterwards.
        op.execute("ALTER TABLE idempotency_records NO FORCE ROW LEVEL SECURITY")

    insert_stmt = sa.text("""
        INSERT INTO idempotency_records
            (tenant_id, key, operation, request_hash, response_json, created_at)
        VALUES
            (:tenant_id, :key, :operation, :request_hash, :response_json, :created_at)
    """).bindparams(sa.bindparam("response_json", type_=sa.JSON()))

    for row in legacy_rows:
        legacy_operation = row["idempotency_operation"] or "create"
        operation = "invoice.upload" if legacy_operation == "upload" else "invoice.create"
        # The legacy route continues to validate the original request against
        # the invoice row. This marker reserves the key for all other routes.
        bind.execute(insert_stmt, {
            "tenant_id": row["tenant_id"],
            "key": row["idempotency_key"],
            "operation": operation,
            "request_hash": row["idempotency_request_hash"] or ("0" * 64),
            "response_json": {"__legacy_invoice_id": row["id"]},
            "created_at": row["created_at"],
        })

    if is_postgres:
        op.execute("ALTER TABLE idempotency_records FORCE ROW LEVEL SECURITY")


def downgrade():
    bind = op.get_bind()
    rows = bind.execute(sa.text("""
        SELECT tenant_id, key, response_json
        FROM idempotency_records
        WHERE operation IN ('invoice.create', 'invoice.upload')
    """)).mappings().all()
    for row in rows:
        payload = row["response_json"]
        if isinstance(payload, str):
            import json
            payload = json.loads(payload)
        if isinstance(payload, dict) and "__legacy_invoice_id" in payload:
            bind.execute(sa.text("""
                DELETE FROM idempotency_records
                WHERE tenant_id = :tenant_id AND key = :key
            """), {"tenant_id": row["tenant_id"], "key": row["key"]})
