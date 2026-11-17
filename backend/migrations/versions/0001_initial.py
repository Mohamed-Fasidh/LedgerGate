"""Frozen explicit schema baseline for the original LedgerGate schema.

This migration is a historical snapshot of the ORM schema at the original
LedgerGate implementation commit. It deliberately does not import current ORM
metadata or call create_all()/drop_all(), so later model changes cannot rewrite
the schema of a fresh installation.

Safety:
* Refuse to create a new baseline over an existing unversioned schema.
* Databases that already recorded 0001_initial are not rerun by Alembic.
* Later revisions remain responsible for every subsequent schema change.
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _assert_empty_unversioned_schema() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    existing.discard("alembic_version")
    if existing:
        names = ", ".join(sorted(existing))
        raise RuntimeError(
            "Refusing to apply LedgerGate baseline to a non-empty unversioned "
            f"schema (found: {names}). Back up and explicitly reconcile or "
            "stamp a reviewed legacy schema; do not drop existing tables."
        )


def upgrade() -> None:
    _assert_empty_unversioned_schema()

    role_enum = sa.Enum("admin", "reviewer", "analyst", name="role")
    invoice_status_enum = sa.Enum(
        "received",
        "extraction_review",
        "processing",
        "review_required",
        "approved",
        "rejected",
        name="invoicestatus",
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("role", role_enum, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_user_email"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("tax_id", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_tenant_supplier_name"),
    )
    op.create_index("ix_suppliers_tenant_id", "suppliers", ["tenant_id"])

    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("po_number", sa.String(length=100), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
        sa.UniqueConstraint("tenant_id", "po_number", name="uq_tenant_po"),
    )
    op.create_index("ix_purchase_orders_tenant_id", "purchase_orders", ["tenant_id"])

    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("invoice_number", sa.String(length=100), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=True),
        sa.Column("purchase_order_id", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("invoice_date", sa.String(length=20), nullable=True),
        sa.Column("status", invoice_status_enum, nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extraction_fields", sa.JSON(), nullable=False),
        sa.Column("reconciliation", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_orders.id"]),
    )
    op.create_index("ix_invoices_tenant_id", "invoices", ["tenant_id"])
    op.create_index(
        "ix_invoice_tenant_number_supplier",
        "invoices",
        ["tenant_id", "invoice_number", "supplier_id"],
    )

    op.create_table(
        "policy_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_policy_documents_tenant_id", "policy_documents", ["tenant_id"])

    op.create_table(
        "agent_memories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("memory_type", sa.String(length=40), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_memories_tenant_id", "agent_memories", ["tenant_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.String(length=80), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_tenant_id", "audit_events", ["tenant_id"])


def downgrade() -> None:
    # Later revisions are downgraded first. Drop dependent tables before their
    # referenced parents; never use metadata.drop_all() for schema rollback.
    for table in (
        "audit_events",
        "agent_memories",
        "policy_documents",
        "invoices",
        "purchase_orders",
        "suppliers",
        "users",
    ):
        op.drop_table(table)

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS invoicestatus")
        op.execute("DROP TYPE IF EXISTS role")
