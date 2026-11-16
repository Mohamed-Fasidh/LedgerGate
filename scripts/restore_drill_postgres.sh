#!/usr/bin/env bash
set -Eeuo pipefail

# Restore into a disposable database on the Compose DB service. This never
# overwrites the configured application database.
backup_file="${1:-}"
[[ -n "$backup_file" && -f "$backup_file" ]] || {
  echo "Usage: $0 path/to/ledgergate-YYYYMMDDTHHMMSSZ.dump" >&2; exit 2;
}
command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
docker compose version >/dev/null
checksum_file="$backup_file.sha256"
[[ -f "$checksum_file" ]] || { echo "Missing checksum file: $checksum_file" >&2; exit 1; }
(cd "$(dirname "$backup_file")" && sha256sum --check "$(basename "$checksum_file")")
docker compose exec -T db pg_restore --list - < "$backup_file" >/dev/null

# Add process and shell-random suffixes so simultaneous drills never collide.
restore_db="ledgergate_restore_drill_$(date -u +%Y%m%d%H%M%S)_$_${RANDOM}"
created=0
cleanup() {
  if [[ "$created" == "1" && "${RESTORE_DRILL_KEEP_DB:-0}" != "1" ]]; then
    docker compose exec -T db sh -c 'dropdb --if-exists -U "$POSTGRES_USER" "$1"' sh "$restore_db" >/dev/null
  fi
}
trap cleanup EXIT

docker compose exec -T db sh -c 'createdb -U "$POSTGRES_USER" "$1"' sh "$restore_db"
created=1
docker compose exec -T db sh -c 'pg_restore --exit-on-error --no-owner --no-acl -U "$POSTGRES_USER" -d "$1" -' sh "$restore_db" < "$backup_file"
verification="$(docker compose exec -T db sh -c 'psql -At -U "$POSTGRES_USER" -d "$1"' sh "$restore_db" <<'SQL'
SELECT (
  EXISTS (SELECT 1 FROM public.alembic_version WHERE version_num <> '')
  AND to_regclass('public.users') IS NOT NULL
  AND to_regclass('public.invoices') IS NOT NULL
)::text;
SQL
)"
[[ "$verification" == "true" ]] || {
  echo "Restore completed but migration version or core users/invoices tables are missing" >&2; exit 1;
}
echo "Restore drill passed for $backup_file into disposable database $restore_db."
if [[ "${RESTORE_DRILL_KEEP_DB:-0}" == "1" ]]; then
  echo "RESTORE_DRILL_KEEP_DB=1: retained $restore_db for inspection."
else
  echo "Disposable restore database will be dropped on exit."
fi
