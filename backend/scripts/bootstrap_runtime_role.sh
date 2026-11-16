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
-- The API needs normal DML, but deletion is restricted to bounded
-- retention/cleanup paths. Revoke the historical broad grant on every run
-- so existing installations converge to the same least-privilege policy.
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO ledgergate_runtime;
REVOKE DELETE ON ALL TABLES IN SCHEMA public FROM ledgergate_runtime;
GRANT DELETE ON TABLE
  registration_attempts,
  login_attempts,
  email_verification_tokens,
  auth_sessions,
  idempotency_records,
  outbound_emails
TO ledgergate_runtime;
-- Audit records are append-only; enforce that at the privilege layer as well
-- as with the database trigger.
REVOKE UPDATE, DELETE ON audit_events FROM ledgergate_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ledgergate_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE ledgergate IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE ON TABLES TO ledgergate_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE ledgergate IN SCHEMA public
  REVOKE DELETE ON TABLES FROM ledgergate_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE ledgergate IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO ledgergate_runtime;
SQL
