"""Explicit PostgreSQL baseline for LedgerGate's current schema.

This revision intentionally does not import ORM metadata or call create_all().
It is a reviewed snapshot of the schema represented by app/models.py.

Safety:
* A non-empty, unversioned schema is rejected rather than silently adopted.
* Existing databases that already recorded 0001_initial are not rerun.
* Downgrade is intentionally destructive and should only be used on disposable
  environments or after a verified backup.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def _assert_empty_unversioned_schema():
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


def upgrade():
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
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("tenant_name", sa.String(150), nullable=False, server_default="Workspace"),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("role", role_enum, nullable=False, server_default="analyst"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mfa_secret_encrypted", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_user_email"),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("tax_id", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_tenant_supplier_name"),
    )
    op.create_index("ix_suppliers_tenant_id", "suppliers", ["tenant_id"])

    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("po_number", sa.String(100), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("total_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], name="fk_purchase_orders_supplier"),
        sa.UniqueConstraint("tenant_id", "po_number", name="uq_tenant_po"),
    )
    op.create_index("ix_purchase_orders_tenant_id", "purchase_orders", ["tenant_id"])

    op.create_table(
        "approval_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("threshold_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("required_approvals", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name="fk_approval_policy_created_by_user"),
        sa.UniqueConstraint(
            "tenant_id", "currency", "version",
            name="uq_approval_policy_tenant_currency_version",
        ),
    )
    op.create_index("ix_approval_policies_tenant_id", "approval_policies", ["tenant_id"])
    op.create_index(
        "ix_approval_policy_tenant_version",
        "approval_policies",
        ["tenant_id", "currency", "version"],
    )

    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("invoice_number", sa.String(100), nullable=False),
        sa.Column("supplier_id", sa.Integer(), nullable=True),
        sa.Column("purchase_order_id", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False, server_default="INR"),
        sa.Column("amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("invoice_date", sa.String(20), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("rejected_by_user_id", sa.Integer(), nullable=True),
        sa.Column("status", invoice_status_enum, nullable=False, server_default="received"),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("file_path", sa.String(500), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extraction_fields", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("reconciliation", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("approval_policy_id", sa.Integer(), nullable=True),
        sa.Column("approval_policy_version", sa.Integer(), nullable=True),
        sa.Column("required_approvals_snapshot", sa.Integer(), nullable=True),
        sa.Column("approval_threshold_snapshot", sa.Numeric(14, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], name="fk_invoice_supplier"),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_orders.id"], name="fk_invoice_purchase_order"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], name="fk_invoice_created_by_user"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"], name="fk_invoice_approved_by_user"),
        sa.ForeignKeyConstraint(["rejected_by_user_id"], ["users.id"], name="fk_invoice_rejected_by_user"),
        sa.ForeignKeyConstraint(["approval_policy_id"], ["approval_policies.id"], name="fk_invoice_approval_policy"),
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
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_policy_documents_tenant_id", "policy_documents", ["tenant_id"])

    op.create_table(
        "agent_memories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("memory_type", sa.String(40), nullable=False, server_default="episodic"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_memories_tenant_id", "agent_memories", ["tenant_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.String(80), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("previous_hash", sa.String(64), nullable=False, server_default="0000000000000000000000000000000000000000000000000000000000000000"),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_hash", name="uq_audit_event_hash"),
    )
    op.create_index("ix_audit_events_tenant_id", "audit_events", ["tenant_id"])

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_idempotency_user"),
        sa.UniqueConstraint("tenant_id", "user_id", "key", name="uq_idempotency_tenant_user_key"),
    )
    op.create_index("ix_idempotency_records_tenant_id", "idempotency_records", ["tenant_id"])
    op.create_index("ix_idempotency_expires_at", "idempotency_records", ["expires_at"])

    op.create_table(
        "invoice_approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], name="fk_invoice_approval_invoice"),
        sa.ForeignKeyConstraint(["reviewer_user_id"], ["users.id"], name="fk_invoice_approval_reviewer"),
        sa.UniqueConstraint(
            "tenant_id", "invoice_id", "reviewer_user_id",
            name="uq_invoice_approval_reviewer",
        ),
    )
    op.create_index("ix_invoice_approvals_tenant_id", "invoice_approvals", ["tenant_id"])
    op.create_index("ix_invoice_approvals_invoice_id", "invoice_approvals", ["invoice_id"])
    op.create_index("ix_invoice_approval_invoice", "invoice_approvals", ["tenant_id", "invoice_id"])

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("jti", sa.String(36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_token_hash", sa.String(64), nullable=True),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_auth_session_user"),
        sa.UniqueConstraint("jti", name="uq_auth_session_jti"),
        sa.UniqueConstraint("refresh_token_hash", name="uq_auth_session_refresh_token_hash"),
    )
    op.create_index("ix_auth_sessions_jti", "auth_sessions", ["jti"], unique=True)
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_tenant_id", "auth_sessions", ["tenant_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_index("ix_auth_sessions_refresh_token_hash", "auth_sessions", ["refresh_token_hash"], unique=True)

    op.create_table(
        "login_throttles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email_key", sa.String(64), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email_key", name="uq_login_throttle_email_key"),
    )
    op.create_index("ix_login_throttles_email_key", "login_throttles", ["email_key"], unique=True)

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_password_reset_user"),
        sa.UniqueConstraint("token_hash", name="uq_password_reset_token_hash"),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
    op.create_index("ix_password_reset_tokens_tenant_id", "password_reset_tokens", ["tenant_id"])
    op.create_index("ix_password_reset_tokens_token_hash", "password_reset_tokens", ["token_hash"], unique=True)
    op.create_index("ix_password_reset_tokens_expires_at", "password_reset_tokens", ["expires_at"])


def downgrade():
    # Alembic runs later revisions' downgrades first (removing RLS policies,
    # triggers and dependent objects). Drop this baseline in FK-safe order.
    for table in (
        "password_reset_tokens",
        "login_throttles",
        "auth_sessions",
        "invoice_approvals",
        "idempotency_records",
        "audit_events",
        "agent_memories",
        "policy_documents",
        "invoices",
        "approval_policies",
        "purchase_orders",
        "suppliers",
        "users",
    ):
        op.drop_table(table)
