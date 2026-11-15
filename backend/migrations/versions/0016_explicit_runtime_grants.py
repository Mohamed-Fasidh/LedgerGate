"""Grant runtime access only to the application tables explicitly approved."""
from alembic import op

revision = "0016_runtime_grants"
down_revision = "0015_active_reset"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # Keep this allowlist explicit. New tables must be reviewed and added here
    # only after their tenant isolation and runtime access requirements are known.
    op.execute("""
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
            public.users,
            public.suppliers,
            public.purchase_orders,
            public.invoices,
            public.policy_documents,
            public.agent_memories,
            public.audit_events,
            public.idempotency_records,
            public.invoice_approvals,
            public.approval_policies,
            public.auth_sessions
        TO ledgergate_runtime
    """)
    # Alembic metadata is read-only for the runtime role; this supports safe
    # schema-version diagnostics without granting migration write privileges.
    op.execute("GRANT SELECT ON TABLE public.alembic_version TO ledgergate_runtime")
    # Sequence access is required for application-generated integer IDs.
    # No future sequence inherits these grants because default privileges are revoked.
    op.execute(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ledgergate_runtime"
    )
    # These records remain accessible only through narrowly scoped SECURITY DEFINER functions.
    op.execute("""
        REVOKE ALL ON TABLE
            public.login_throttles,
            public.password_reset_tokens
        FROM ledgergate_runtime
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute("""
        REVOKE ALL ON TABLE
            public.users,
            public.suppliers,
            public.purchase_orders,
            public.invoices,
            public.policy_documents,
            public.agent_memories,
            public.audit_events,
            public.idempotency_records,
            public.invoice_approvals,
            public.approval_policies,
            public.auth_sessions
        FROM ledgergate_runtime
    """)
    op.execute("REVOKE ALL ON TABLE public.alembic_version FROM ledgergate_runtime")
    op.execute(
        "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM ledgergate_runtime"
    )
