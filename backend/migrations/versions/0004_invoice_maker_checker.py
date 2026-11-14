"""Track invoice creators and enforce maker-checker decisions.

Columns and foreign keys may already exist when the initial migration creates
its schema from current ORM metadata. Add only missing objects.
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_invoice_maker_checker"
down_revision = "0003_audit_hash_chain"
branch_labels = None
depends_on = None

FIELDS = (
    ("created_by_user_id", "fk_invoice_created_by_user"),
    ("approved_by_user_id", "fk_invoice_approved_by_user"),
    ("rejected_by_user_id", "fk_invoice_rejected_by_user"),
)


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {item["name"] for item in inspector.get_columns("invoices")}
    for column, _constraint in FIELDS:
        if column not in existing_columns:
            op.add_column("invoices", sa.Column(column, sa.Integer(), nullable=True))

    inspector = sa.inspect(bind)
    foreign_keys = {
        (tuple(item.get("constrained_columns") or ()), item.get("referred_table"))
        for item in inspector.get_foreign_keys("invoices")
    }
    for column, constraint in FIELDS:
        if ((column,), "users") not in foreign_keys:
            op.create_foreign_key(constraint, "invoices", "users", [column], ["id"])


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    foreign_keys = inspector.get_foreign_keys("invoices")
    for column, constraint in reversed(FIELDS):
        found = next((item for item in foreign_keys
                      if item.get("name") == constraint
                      or (item.get("constrained_columns") == [column]
                          and item.get("referred_table") == "users")), None)
        if found and found.get("name"):
            op.drop_constraint(found["name"], "invoices", type_="foreignkey")
        if column in {item["name"] for item in sa.inspect(bind).get_columns("invoices")}:
            op.drop_column("invoices", column)
