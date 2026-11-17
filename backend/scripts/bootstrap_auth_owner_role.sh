#!/bin/sh
set -eu
: "${MIGRATION_DATABASE_URL:?MIGRATION_DATABASE_URL must point to a privileged migration/bootstrap account}"
PSQL_DATABASE_URL=$(printf '%s' "$MIGRATION_DATABASE_URL" | sed 's#^postgresql+psycopg://#postgresql://#')
psql "$PSQL_DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
SELECT format(
  'CREATE ROLE ledgergate_auth_owner NOLOGIN BYPASSRLS'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ledgergate_auth_owner')
\gexec

ALTER ROLE ledgergate_auth_owner NOLOGIN BYPASSRLS;
SELECT format('GRANT ledgergate_auth_owner TO %I', current_user)
\gexec
GRANT USAGE ON SCHEMA public TO ledgergate_auth_owner;
-- The role owns only the reviewed lookup functions; it needs no persistent
-- ability to create arbitrary objects in the application schema.
REVOKE CREATE ON SCHEMA public FROM ledgergate_auth_owner;

SELECT format('CREATE ROLE ledgergate_agent_queue_owner NOLOGIN BYPASSRLS')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ledgergate_agent_queue_owner')
\gexec

ALTER ROLE ledgergate_agent_queue_owner NOLOGIN BYPASSRLS;
SELECT format('GRANT ledgergate_agent_queue_owner TO %I', current_user)
\gexec
GRANT USAGE ON SCHEMA public TO ledgergate_agent_queue_owner;
REVOKE CREATE ON SCHEMA public FROM ledgergate_agent_queue_owner;
SQL
