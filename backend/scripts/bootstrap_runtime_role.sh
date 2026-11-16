#!/bin/sh
set -eu
: "${MIGRATION_DATABASE_URL:?MIGRATION_DATABASE_URL must point to the migration owner account}"
: "${DB_RUNTIME_PASSWORD:?DB_RUNTIME_PASSWORD must be set}"
psql "$MIGRATION_DATABASE_URL" -v ON_ERROR_STOP=1 -v runtime_password="$DB_RUNTIME_PASSWORD" <<'SQL'
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
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ledgergate_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ledgergate_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE ledgergate IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ledgergate_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE ledgergate IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO ledgergate_runtime;
SQL
