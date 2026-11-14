"""Expand invoice extraction, policy retrieval and agent memory."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision="0002_expand_workflows"
down_revision="0001_initial"
branch_labels=None
depends_on=None

def _columns(table):
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}

def _tables():
    return set(inspect(op.get_bind()).get_table_names())

def upgrade():
    bind=op.get_bind()
    tables=_tables()
    if "users" in tables and "tenant_name" not in _columns("users"):
        op.add_column("users",sa.Column("tenant_name",sa.String(length=150),nullable=False,server_default="Workspace"))
    if "invoices" in tables:
        cols=_columns("invoices")
        if "extraction_fields" not in cols:
            op.add_column("invoices",sa.Column("extraction_fields",sa.JSON(),nullable=False,server_default=sa.text("'{}'")))
        if "amount" in cols and bind.dialect.name=="postgresql":
            op.alter_column("invoices","amount",existing_type=sa.Numeric(14,2),nullable=True)
    if bind.dialect.name=="postgresql":
        op.execute("ALTER TYPE invoicestatus ADD VALUE IF NOT EXISTS 'extraction_review'")
    tables=_tables()
    if "policy_documents" not in tables:
        op.create_table("policy_documents",
            sa.Column("id",sa.Integer(),primary_key=True),
            sa.Column("tenant_id",sa.String(64),nullable=False,index=True),
            sa.Column("title",sa.String(255),nullable=False),
            sa.Column("body",sa.Text(),nullable=False),
            sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))
    tables=_tables()
    if "agent_memories" not in tables:
        op.create_table("agent_memories",
            sa.Column("id",sa.Integer(),primary_key=True),
            sa.Column("tenant_id",sa.String(64),nullable=False,index=True),
            sa.Column("memory_type",sa.String(40),nullable=False,server_default="episodic"),
            sa.Column("content",sa.Text(),nullable=False),
            sa.Column("metadata_json",sa.JSON(),nullable=False,server_default=sa.text("'{}'")),
            sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))

def downgrade():
    # Intentionally non-destructive: preserve business data during rollback.
    pass
