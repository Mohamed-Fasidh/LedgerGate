"""Add hash-chained immutable audit events.

The initial migration builds tables from current ORM metadata, so on a fresh
install these columns may already exist. This migration supports both that
case and databases created by earlier model versions.
"""
import hashlib
import json
from datetime import timezone

from alembic import op
import sqlalchemy as sa

revision = "0003_audit_hash_chain"
down_revision = "0002_tenant_rls"
branch_labels = None
depends_on = None

GENESIS_HASH = "0" * 64


def _event_hash(previous_hash, row):
    created_at = row["created_at"]
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    payload = {
        "tenant_id": row["tenant_id"],
        "actor_user_id": row["actor_user_id"],
        "entity_type": row["entity_type"],
        "entity_id": str(row["entity_id"]),
        "action": row["action"],
        "details": row["details"] or {},
        "created_at": created_at.astimezone(timezone.utc).isoformat(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(previous_hash.encode("ascii") + b"\\n" + canonical.encode("utf-8")).hexdigest()


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns("audit_events")}

    if "previous_hash" not in columns:
        op.add_column("audit_events", sa.Column("previous_hash", sa.String(64), nullable=True))
    if "event_hash" not in columns:
        op.add_column("audit_events", sa.Column("event_hash", sa.String(64), nullable=True))

    rows = bind.execute(sa.text(
        "SELECT id, tenant_id, actor_user_id, entity_type, entity_id, action, details, created_at "
        "FROM audit_events ORDER BY tenant_id, id"
    )).mappings().all()
    heads = {}
    for row in rows:
        previous = heads.get(row["tenant_id"], GENESIS_HASH)
        current = _event_hash(previous, row)
        bind.execute(
            sa.text("UPDATE audit_events SET previous_hash=:previous, event_hash=:current WHERE id=:id"),
            {"previous": previous, "current": current, "id": row["id"]},
        )
        heads[row["tenant_id"]] = current

    inspector = sa.inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns("audit_events")}
    if columns["previous_hash"]["nullable"]:
        op.alter_column("audit_events", "previous_hash", existing_type=sa.String(64), nullable=False)
    if columns["event_hash"]["nullable"]:
        op.alter_column("audit_events", "event_hash", existing_type=sa.String(64), nullable=False)

    unique_columns = {
        tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints("audit_events")
    }
    unique_columns.update(
        tuple(item.get("column_names") or ())
        for item in inspector.get_indexes("audit_events") if item.get("unique")
    )
    if ("event_hash",) not in unique_columns:
        op.create_unique_constraint("uq_audit_event_hash", "audit_events", ["event_hash"])

    if bind.dialect.name == "postgresql":
        op.execute("""
        CREATE OR REPLACE FUNCTION prevent_audit_event_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events are append-only; UPDATE and DELETE are prohibited'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql;
        """)
        op.execute("""
        DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events;
        CREATE TRIGGER audit_events_immutable
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_event_mutation();
        """)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events")
        op.execute("DROP FUNCTION IF EXISTS prevent_audit_event_mutation()")
    inspector = sa.inspect(bind)
    unique_columns = {
        tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints("audit_events")
    }
    if ("event_hash",) in unique_columns:
        op.drop_constraint("uq_audit_event_hash", "audit_events", type_="unique")
    op.drop_column("audit_events", "event_hash")
    op.drop_column("audit_events", "previous_hash")
