"""Add a per-tenant SHA-256 audit event chain."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
import hashlib
import json
from datetime import timezone

revision = "0009_audit_hash_chain"
down_revision = "0008_multi_stage_approvals"
branch_labels = None
depends_on = None

GENESIS = "0" * 64

def _iso(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()

def _digest(previous, payload):
    canonical = json.dumps(
        {"previous_hash": previous, "event": payload},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # The existing append-only trigger and FORCE RLS protect normal runtime
        # writes. Temporarily suspend them inside this schema migration only so
        # the migration owner can backfill historical rows, then restore both.
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events")
        op.execute("ALTER TABLE audit_events NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE audit_events DISABLE ROW LEVEL SECURITY")
    columns = {c["name"] for c in inspect(bind).get_columns("audit_events")}
    if "previous_hash" not in columns:
        op.add_column("audit_events", sa.Column("previous_hash", sa.String(64), nullable=True))
    if "event_hash" not in columns:
        op.add_column("audit_events", sa.Column("event_hash", sa.String(64), nullable=True))

    rows = bind.execute(sa.text("""
        SELECT id, tenant_id, actor_user_id, entity_type, entity_id, action, details, created_at
        FROM audit_events ORDER BY tenant_id, id
    """)).mappings().all()
    heads = {}
    for row in rows:
        previous = heads.get(row["tenant_id"], GENESIS)
        payload = {
            "tenant_id": row["tenant_id"],
            "actor_user_id": row["actor_user_id"],
            "entity_type": row["entity_type"],
            "entity_id": str(row["entity_id"]),
            "action": row["action"],
            "details": json.loads(row["details"]) if isinstance(row["details"], str) else (row["details"] or {}),
            "created_at": _iso(row["created_at"]),
        }
        digest = _digest(previous, payload)
        bind.execute(sa.text("""
            UPDATE audit_events SET previous_hash=:previous, event_hash=:digest WHERE id=:id
        """), {"previous": previous, "digest": digest, "id": row["id"]})
        heads[row["tenant_id"]] = digest

    with op.batch_alter_table("audit_events") as batch:
        batch.alter_column("previous_hash", existing_type=sa.String(64), nullable=False)
        batch.alter_column("event_hash", existing_type=sa.String(64), nullable=False)
        batch.create_unique_constraint("uq_audit_events_event_hash", ["event_hash"])

    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE audit_events FORCE ROW LEVEL SECURITY")
        op.execute("""
            CREATE TRIGGER audit_events_no_update
            BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION ledgergate_prevent_audit_mutation()
        """)

def downgrade():
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_constraint("uq_audit_events_event_hash", type_="unique")
        batch.drop_column("event_hash")
        batch.drop_column("previous_hash")
