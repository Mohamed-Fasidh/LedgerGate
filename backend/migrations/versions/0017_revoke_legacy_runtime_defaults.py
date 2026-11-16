"""Remove legacy broad default grants to the runtime role during upgrades."""
from alembic import op

revision = "0017_runtime_defaults"
down_revision = "0016_runtime_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # Older installations may have been initialized before restrictive
    # defaults were introduced. Revoke any existing default table/sequence
    # ACL that names ledgergate_runtime, regardless of which owner role set it.
    op.execute("""
        DO $$
        DECLARE
            owner_name text;
        BEGIN
            FOR owner_name IN
                SELECT DISTINCT owner_role.rolname
                FROM pg_default_acl AS defaults
                JOIN pg_roles AS owner_role
                  ON owner_role.oid = defaults.defaclrole
                CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl
                JOIN pg_roles AS grantee_role
                  ON grantee_role.oid = acl.grantee
                WHERE defaults.defaclnamespace = 'public'::regnamespace
                  AND defaults.defaclobjtype IN ('r', 'S')
                  AND grantee_role.rolname = 'ledgergate_runtime'
            LOOP
                EXECUTE format(
                    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
                    'REVOKE ALL ON TABLES FROM ledgergate_runtime',
                    owner_name
                );
                EXECUTE format(
                    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
                    'REVOKE ALL ON SEQUENCES FROM ledgergate_runtime',
                    owner_name
                );
            END LOOP;
        END
        $$;
    """)


def downgrade() -> None:
    # Deliberately do not restore broad default grants. Reintroducing them
    # would silently expose future tables and sequences to the runtime role.
    pass
