"""Regression checks for the frozen LedgerGate Alembic baseline."""
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from app.database import engine


def test_initial_migration_is_explicit_and_fails_closed_for_unversioned_data():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0001_initial.py"
    )
    source = migration_path.read_text(encoding="utf-8")
    assert "Base.metadata.create_all" not in source
    assert "Base.metadata.drop_all" not in source
    assert "op.create_table(" in source
    assert "_assert_empty_unversioned_schema()" in source
    assert "Refusing to apply LedgerGate baseline" in source
    assert "existing.discard(\"alembic_version\")" in source


@pytest.mark.skipif(
    engine.dialect.name != "postgresql",
    reason="The frozen migration contract is verified against PostgreSQL in CI",
)
def test_postgresql_schema_matches_frozen_baseline_and_all_following_revisions():
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    expected_tables = {
        "users",
        "suppliers",
        "purchase_orders",
        "invoices",
        "policy_documents",
        "agent_memories",
        "audit_events",
        "login_attempts",
        "approval_policies",
        "invoice_approvals",
        "auth_sessions",
        "idempotency_records",
        "registration_attempts",
        "password_reset_tokens",
        "email_verification_tokens",
        "outbound_emails",
        "alembic_version",
    }
    assert expected_tables.issubset(tables)

    expected_columns = {
        "users": {
            "tenant_id", "tenant_name", "email", "mfa_enabled",
            "mfa_secret_encrypted", "email_verified", "auth_token_version",
            "mfa_recovery_code_hashes",
        },
        "invoices": {
            "extraction_fields", "reconciliation", "created_by_user_id",
            "version", "idempotency_key", "idempotency_operation",
            "idempotency_request_hash", "approval_policy_id",
            "approval_policy_version", "required_approvals_snapshot",
            "approval_threshold_snapshot",
        },
        "audit_events": {"previous_hash", "event_hash"},
        "auth_sessions": {"refresh_token_hash", "refresh_expires_at"},
        "password_reset_tokens": {
            "tenant_id", "token_hash", "expires_at", "used_at",
        },
        "email_verification_tokens": {
            "tenant_id", "token_hash", "expires_at", "used_at",
        },
        "outbound_emails": {
            "recipient", "message_type", "encrypted_payload", "status",
            "attempts", "locked_at", "lease_id",
        },
    }
    for table, columns in expected_columns.items():
        actual = {column["name"] for column in inspector.get_columns(table)}
        assert columns.issubset(actual), f"{table} is missing {columns - actual}"

    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert revision == "0026_preauth_identity_rls"

        unique_constraints = {
            item["name"]
            for item in inspector.get_unique_constraints("approval_policies")
        }
        assert "uq_approval_policy_tenant_currency_version" in unique_constraints

        enum_values = connection.execute(text("""
            SELECT t.typname, e.enumlabel
            FROM pg_type AS t
            JOIN pg_enum AS e ON e.enumtypid = t.oid
            WHERE t.typname IN ('role', 'invoicestatus')
            ORDER BY t.typname, e.enumsortorder
        """)).all()
        labels = {}
        for enum_name, label in enum_values:
            labels.setdefault(enum_name, set()).add(label)
        assert labels["role"] == {"admin", "reviewer", "analyst"}
        assert labels["invoicestatus"] == {
            "received", "extraction_review", "processing",
            "review_required", "approved", "rejected",
        }
