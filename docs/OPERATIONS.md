# Operations runbook

## Health checks
- API: `GET /health`
- API schema: `/docs`
- Database: Docker health check `pg_isready`
- Frontend: `http://localhost:3000`

## Common commands
```bash
docker compose ps
docker compose logs -f api
docker compose logs -f db
docker compose down
docker compose up --build -d
```

## Database backup and restore drills

Run these scripts from the repository root with the Compose database service healthy:

```bash
# Create a custom-format, archive-validated dump and SHA-256 sidecar.
bash scripts/backup_postgres.sh

# Verify checksum and restore into a disposable database, then drop it.
bash scripts/restore_drill_postgres.sh ./backups/ledgergate-YYYYMMDDTHHMMSSZ.dump
```

- Backups are written under `./backups` by default with restrictive permissions. Set `BACKUP_DIR` to a protected, capacity-managed destination and `BACKUP_RETENTION_DAYS` (1–3650; default 30) to control local cleanup.
- The backup script validates the PostgreSQL custom archive before publishing it and writes a SHA-256 sidecar. Copy both files to encrypted off-host storage; a local dump is not a disaster-recovery strategy.
- The restore drill checks the checksum, restores into a newly created disposable database, and confirms the Alembic version table exists. It never restores over the configured application database. Set `RESTORE_DRILL_KEEP_DB=1` only when you intentionally want to inspect the temporary database; remove it manually afterwards.
- These scripts do not back up PostgreSQL roles/globals, object-storage invoice files, secrets, or cloud configuration. Back up and test those separately. Production backups should use managed encrypted backups/PITR and an independently secured off-site copy.
- Run restore drills against an isolated recovery environment, not a busy production database. Record duration, backup age, row counts, and recovery-point/recovery-time results.

## Incident response
1. Disable affected user accounts and rotate credentials.
2. Preserve logs and audit events.
3. Identify affected tenant and record IDs.
4. Restore from a known-good backup if integrity is compromised.
5. Notify security/privacy owners and follow applicable legal obligations.
6. Document root cause and corrective actions.


## Audit signing-key rotation and verification

- Assign every signing-key version a unique, non-secret `AUDIT_EXPORT_SIGNING_KEY_ID`. Never reuse an ID for different key material.
- Before switching the active key, add the new key to the independent verification keyring and test a signed sample with it.
- Deploy the new `AUDIT_EXPORT_SIGNING_KEY` and matching ID together. Keep previous keys in the verifier's protected keyring for at least the full Object Lock retention period of every export signed with those keys.
- Use `verify_audit_export_signature_with_keyring(document, keyring)` in an independent verification process. The helper selects the verification key by the signed document's `signing_key_id`; unknown IDs and invalid signatures fail verification.
- Use `verify_audit_export_document(document, keyring)` when validating a downloaded export. In addition to the key-ID-selected HMAC, it validates event count, tenant consistency, ascending event IDs, first/last metadata, every previous-hash link, and every event hash. A valid HMAC alone proves the export payload was signed; this structural check also verifies that the included event sequence is internally consistent.
- Restrict keyring access, log verification operations without logging key material, maintain protected offline recovery copies, and document emergency revocation/re-signing procedures. Do not place historical secrets in source control or ordinary application logs.
- Each audit export attempt uses a fresh UUID in both its signed payload and S3 object key, even when the event range is identical. This avoids treating a retry as an overwrite of a prior COMPLIANCE-locked object. A successful S3 write followed by a failed database commit can still leave an unreferenced immutable object; periodically reconcile the WORM bucket against `immutable_export_created` audit records and retain unexplained objects for investigation rather than deleting them.
- The application currently accepts one active signing key through environment configuration. Historical key retention and independent verification-keyring storage remain operator responsibilities; this code does not implement a managed KMS keyring or automatic rotation.
