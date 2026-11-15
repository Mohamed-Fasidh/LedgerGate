"""Scope approval policy versions by tenant and currency."""
from alembic import op
import sqlalchemy as sa

revision = "0012_currency_policy_versions"
down_revision = "0011_password_reset_tokens"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    inspector = sa.inspect(bind)
    constraints = {item["name"] for item in inspector.get_unique_constraints("approval_policies")}
    if "uq_approval_policy_tenant_version" in constraints:
        op.drop_constraint(
            "uq_approval_policy_tenant_version",
            "approval_policies",
            type_="unique",
        )
    if "uq_approval_policy_tenant_currency_version" not in constraints:
        op.create_unique_constraint(
            "uq_approval_policy_tenant_currency_version",
            "approval_policies",
            ["tenant_id", "currency", "version"],
        )

    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("approval_policies")}
    if "ix_approval_policy_tenant_version" in indexes:
        op.drop_index("ix_approval_policy_tenant_version", table_name="approval_policies")
    op.create_index(
        "ix_approval_policy_tenant_version",
        "approval_policies",
        ["tenant_id", "currency", "version"],
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("approval_policies")}
    if "ix_approval_policy_tenant_version" in indexes:
        op.drop_index("ix_approval_policy_tenant_version", table_name="approval_policies")
    op.create_index(
        "ix_approval_policy_tenant_version",
        "approval_policies",
        ["tenant_id", "version"],
    )
    constraints = {item["name"] for item in sa.inspect(bind).get_unique_constraints("approval_policies")}
    if "uq_approval_policy_tenant_currency_version" in constraints:
        op.drop_constraint(
            "uq_approval_policy_tenant_currency_version",
            "approval_policies",
            type_="unique",
        )
    if "uq_approval_policy_tenant_version" not in constraints:
        op.create_unique_constraint(
            "uq_approval_policy_tenant_version",
            "approval_policies",
            ["tenant_id", "version"],
        )
