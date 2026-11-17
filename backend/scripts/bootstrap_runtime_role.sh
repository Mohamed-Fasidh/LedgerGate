#!/bin/sh
set -eu
: "${MIGRATION_DATABASE_URL:?MIGRATION_DATABASE_URL must point to the migration owner account}"
: "${DB_RUNTIME_PASSWORD:?DB_RUNTIME_PASSWORD must be set}"
PSQL_DATABASE_URL=$(printf '%s' "$MIGRATION_DATABASE_URL" | sed 's#^postgresql+psycopg://#postgresql://#')
psql "$PSQL_DATABASE_URL" -v ON_ERROR_STOP=1 -v runtime_password="$DB_RUNTIME_PASSWORD" <<'SQL'
SELECT format(
  'CREATE ROLE ledgergate_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD %L',
  :'runtime_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ledgergate_runtime')
\gexec
SELECT format('ALTER ROLE ledgergate_runtime PASSWORD %L', :'runtime_password')
\gexec

GRANT CONNECT ON DATABASE ledgergate TO ledgergate_runtime;
GRANT USAGE ON SCHEMA public TO ledgergate_runtime;

-- Remove the legacy blanket grants before rebuilding the reviewed allowlist.
-- This is intentionally rerunnable during container restarts and deployments.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM ledgergate_runtime;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM ledgergate_runtime;

GRANT SELECT, INSERT, UPDATE ON TABLE
  public.users,
  public.suppliers,
  public.purchase_orders,
  public.invoices,
  public.policy_documents,
  public.agent_memories,
  public.audit_events,
  public.registration_attempts,
  public.login_attempts,
  public.approval_policies,
  public.invoice_approvals,
  public.auth_sessions,
  public.idempotency_records,
  public.password_reset_tokens,
  public.email_verification_tokens,
  public.outbound_emails,
  public.agent_tasks
TO ledgergate_runtime;

-- Runtime deletion is limited to bounded retention/cleanup tables. Business
-- records, user accounts, approvals, password-reset tokens, and audit evidence
-- remain non-deletable by the application role.
GRANT DELETE ON TABLE
  public.registration_attempts,
  public.login_attempts,
  public.auth_sessions,
  public.idempotency_records,
  public.email_verification_tokens,
  public.outbound_emails
TO ledgergate_runtime;

-- Runtime needs schema-version diagnostics, never migration write access.

GRANT SELECT ON TABLE public.alembic_version TO ledgergate_runtime;

-- Existing application-generated IDs need sequence usage. Future sequences
-- receive no automatic grant and must be added through a reviewed migration.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ledgergate_runtime;

-- Authentication lookup functions are the only cross-tenant discovery
-- boundary. Grant EXECUTE after the runtime role has been created; PUBLIC
-- execution was revoked by migration 0026.
GRANT EXECUTE ON FUNCTION public.auth_lookup_user(text) TO ledgergate_runtime;
GRANT EXECUTE ON FUNCTION public.auth_email_exists(text) TO ledgergate_runtime;
GRANT EXECUTE ON FUNCTION public.auth_lookup_password_reset(character varying) TO ledgergate_runtime;
GRANT EXECUTE ON FUNCTION public.auth_lookup_email_verification(character varying) TO ledgergate_runtime;
GRANT EXECUTE ON FUNCTION public.claim_agent_task(text, text, integer) TO ledgergate_runtime;

-- Revoke inherited defaults for the actual migration owner (which may differ
-- between local Compose, CI, and a managed production database).
DO $$
BEGIN
  EXECUTE format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public REVOKE ALL ON TABLES FROM ledgergate_runtime',
    current_user
  );
  EXECUTE format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public REVOKE ALL ON SEQUENCES FROM ledgergate_runtime',
    current_user
  );
END
$$;
SQL
