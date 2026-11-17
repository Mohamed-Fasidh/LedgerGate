"""Scope approval amount ranges by invoice currency.

Existing policies are backfilled as INR to preserve historical behavior.
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_currency_approval"
down_revision = "0027_tenant_owned_references"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "approval_policies",
        sa.Column("currency", sa.String(length=3), server_default="INR", nullable=False),
    )
    op.create_index(
        "ix_approval_policies_tenant_currency_min_id",
        "approval_policies",
        ["tenant_id", "currency", "min_amount", "id"],
        unique=False,
    )
 

def downgrade() -> None:
    op.drop_index("ix_approval_policies_tenant_currency_min_id", table_name="approval_policies")
    op.drop_column("approval_policies", "currency")
