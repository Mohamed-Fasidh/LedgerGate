"""Stop future migration objects inheriting blanket runtime-role privileges.

The bootstrap script rebuilds the current-table allowlist after migrations.
This revision also removes any default ACL left by the migration owner on
existing installations where the runtime role already exists.
"""
from alembic import op

revision = "0025_runtime_privilege_defaults"
down_revision = "0024_email_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ledgergate_runtime') THEN
                EXECUTE format(
                    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
                    'REVOKE ALL ON TABLES FROM ledgergate_runtime',
                    current_user
                );
                EXECUTE format(
                    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
                    'REVOKE ALL ON SEQUENCES FROM ledgergate_runtime',
                    current_user
                );
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # Deliberately do not restore blanket grants: doing so would silently
    # expose future tables and sequences. Regranting is an explicit operator
    # action and is not part of a safe schema downgrade.
    pass
