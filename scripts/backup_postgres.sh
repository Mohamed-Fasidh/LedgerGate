#!/usr/bin/env bash
set -Eeuo pipefail

# Run from the repository root. The database credentials remain inside the
# Compose database container; the dump is streamed to the host.
command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
docker compose version >/dev/null
backup_dir="${BACKUP_DIR:-./backups}"
retention_days="${BACKUP_RETENTION_DAYS:-30}"
[[ "$retention_days" =~ ^[0-9]+$ ]] && (( retention_days >= 1 && retention_days <= 3650 )) || {
  echo "BACKUP_RETENTION_DAYS must be an integer from 1 to 3650" >&2; exit 2;
}
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
final="$backup_dir/ledgergate-$stamp.dump"
tmp="$final.partial"
checksum_tmp="$final.sha256.partial"
published=0
cleanup() {
  rm -f "$tmp" "$checksum_tmp"
  if [[ "$published" != "1" ]]; then
    rm -f "$final" "$final.sha256"
  fi
}
trap cleanup EXIT

docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl' > "$tmp"
[[ -s "$tmp" ]] || { echo "Backup output is empty" >&2; exit 1; }
# Validate archive readability before publishing it.
docker compose exec -T db pg_restore --list - < "$tmp" >/dev/null
mv "$tmp" "$final"
(
  cd "$(dirname "$final")"
  sha256sum "$(basename "$final")" > "$(basename "$checksum_tmp")"
)
chmod 600 "$final" "$checksum_tmp"
mv "$checksum_tmp" "$final.sha256"
published=1

# Retention only removes completed LedgerGate dumps and their checksum files.
find "$backup_dir" -maxdepth 1 -type f -name 'ledgergate-*.dump' -mtime "+$retention_days" -print0 |
while IFS= read -r -d '' old; do
  rm -f -- "$old" "$old.sha256"
done

echo "Backup created and archive-validated: $final"
echo "Checksum: $final.sha256"
