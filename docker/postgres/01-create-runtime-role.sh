#!/bin/sh
set -eu

: "${LEDGERGATE_RUNTIME_PASSWORD:=runtime_local_only_change_me}"

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=runtime_password="$LEDGERGATE_RUNTIME_PASSWORD" <<'SQL'
CREATE ROLE ledgergate_runtime
  LOGIN PASSWORD :'runtime_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;

GRANT CONNECT ON DATABASE ledgergate TO ledgergate_runtime;
GRANT USAGE ON SCHEMA public TO ledgergate_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ledgergate_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ledgergate_runtime;

ALTER DEFAULT PRIVILEGES FOR ROLE ledgergate IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ledgergate_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE ledgergate IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO ledgergate_runtime;
SQL
