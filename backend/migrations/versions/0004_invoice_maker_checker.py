"""Track invoice creators and enforce maker-checker decisions."""
from alembic import op
import sqlalchemy as sa

revision = "0004_invoice_maker_checker"
down_revision = "0003_audit_hash_chain"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("invoices", sa.Column("created_by_user_id", sa.Integer(), nullable=True))
    op.add_column("invoices", sa.Column("approved_by_user_id", sa.Integer(), nullable=True))
    op.add_column("invoices", sa.Column("rejected_by_user_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_invoice_created_by_user", "invoices", "users", ["created_by_user_id"], ["id"])
    op.create_foreign_key("fk_invoice_approved_by_user", "invoices", "users", ["approved_by_user_id"], ["id"])
    op.create_foreign_key("fk_invoice_rejected_by_user", "invoices", "users", ["rejected_by_user_id"], ["id"])


def downgrade():
    op.drop_constraint("fk_invoice_rejected_by_user", "invoices", type_="foreignkey")
    op.drop_constraint("fk_invoice_approved_by_user", "invoices", type_="foreignkey")
    op.drop_constraint("fk_invoice_created_by_user", "invoices", type_="foreignkey")
    op.drop_column("invoices", "rejected_by_user_id")
    op.drop_column("invoices", "approved_by_user_id")
    op.drop_column("invoices", "created_by_user_id")
