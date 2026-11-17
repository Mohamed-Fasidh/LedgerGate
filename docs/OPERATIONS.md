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
bash scripts/restore_drill_postgres.sh ./backups/ledgergate-YYYYMMDDTHHMMSSZ-unique-token.dump
```

- Backups are written under `./backups` by default with restrictive permissions. Set `BACKUP_DIR` to a protected, capacity-managed destination and `BACKUP_RETENTION_DAYS` (1–3650; default 30) to control local cleanup.
- The backup script uses a collision-resistant temporary filename and a unique final filename, validates the PostgreSQL custom archive before publishing it, writes the SHA-256 sidecar to a temporary file, and atomically publishes the checksum last. Failed/incomplete runs clean up the dump and sidecar so they are not mistaken for a complete backup. Copy both files to encrypted off-host storage; a local dump is not a disaster-recovery strategy.
- The restore drill checks the checksum, restores into a uniquely named disposable database, and confirms that the Alembic version table contains a version and the core users and invoices tables exist. It never restores over the configured application database. Set `RESTORE_DRILL_KEEP_DB=1` only when you intentionally want to inspect the temporary database; remove it manually afterwards.
- These scripts do not back up PostgreSQL roles/globals, object-storage invoice files, secrets, or cloud configuration. Back up and test those separately. Production backups should use managed encrypted backups/PITR and an independently secured off-site copy.
- Run restore drills against an isolated recovery environment, not a busy production database. Record duration, backup age, row counts, and recovery-point/recovery-time results.
- CI exercises backup publication with a fake Docker/PostgreSQL command boundary, including overlapping runs and failure cleanup. It also exercises restore checksum rejection, restore failure cleanup, required schema checks, and the explicit keep-database option. These tests validate script behavior; they do not replace a real restore drill against a separately provisioned PostgreSQL recovery instance.



### Offline audit-export verification CLI

After downloading an export object from the WORM bucket, verify it outside the API process. From the `backend/` directory, create a protected JSON keyring whose keys are indexed by the exact non-secret signing key IDs in the exports:

```json
{
  "audit-key-2026-11": "REPLACE_WITH_THE_32_OR_MORE_CHARACTER_SECRET"
}
```

On Linux/macOS, restrict the keyring file before use:

```bash
chmod 600 /secure/path/audit-keyring.json
python -m scripts.verify_audit_export --document /secure/path/export.json --keyring-file /secure/path/audit-keyring.json
```

The CLI returns exit code 0 only when the HMAC, export metadata, tenant consistency, and complete included event hash chain validate; 1 means the export is invalid; 2 means the input/keyring could not be read or is unsafe. It never accepts secret values as command-line arguments and does not print key material. On Windows, store the keyring in a directory restricted to the operator account because POSIX mode checks are not available. Keep the keyring out of source control and ordinary logs.

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
- Tenant audit-chain verification reads events in bounded pages (default 1,000; maximum 10,000) rather than materializing the entire history in application memory. The admin verification endpoint and immutable exporter acquire the shared tenant writer lock first, so all pages observe a stable chain while the verification transaction is active.
- Before verification, the exporter acquires the same PostgreSQL transaction-scoped tenant advisory lock used by audit writers. This prevents an in-flight writer with an earlier global sequence ID from committing after the export cursor has advanced. The lock remains held through the export transaction; monitor export duration because it briefly serializes audit writes for that tenant.
- Export batches are bounded by the exact `last_event_id` returned from the full-chain verification. Events appended concurrently after that verification are deliberately deferred to the next export request, which verifies the chain again before exporting them.
- Use `verify_audit_export_document(document, keyring)` when validating a downloaded export. In addition to the key-ID-selected HMAC, it validates event count, tenant consistency, ascending event IDs, first/last metadata, every previous-hash link, and every event hash. A valid HMAC alone proves the export payload was signed; this structural check also verifies that the included event sequence is internally consistent.
- Restrict keyring access, log verification operations without logging key material, maintain protected offline recovery copies, and document emergency revocation/re-signing procedures. Do not place historical secrets in source control or ordinary application logs.
- Each audit export attempt uses a fresh UUID in both its signed payload and S3 object key, even when the event range is identical. This avoids treating a retry as an overwrite of a prior COMPLIANCE-locked object. A successful S3 write followed by a failed database commit can still leave an unreferenced immutable object; periodically reconcile the WORM bucket against `immutable_export_created` audit records and retain unexplained objects for investigation rather than deleting them.
- The application currently accepts one active signing key through environment configuration. Historical key retention and independent verification-keyring storage remain operator responsibilities; this code does not implement a managed KMS keyring or automatic rotation.

## Invoice object-storage reconciliation

The storage inventory command compares generated invoice objects with `invoices.file_path` references. It is a maintenance operation, not part of API startup.

Run a dry-run first:

```bash
cd backend
RECONCILIATION_DATABASE_URL="$RECONCILIATION_DATABASE_URL" python -m scripts.reconcile_storage
```

The database URL must use a dedicated PostgreSQL maintenance role that:
- is not `ledgergate_runtime` and is not a superuser;
- has `BYPASSRLS` so the inventory includes every tenant;
- has read-only access to the invoice table.

The command sets its database transaction to read-only. It also needs object-storage list access. Local storage is supported for development.

Deletion is disabled by default. To remove confirmed orphan objects, use both explicit flags:

```bash
RECONCILIATION_DATABASE_URL="$RECONCILIATION_DATABASE_URL" python -m scripts.reconcile_storage --delete-orphans --confirm-delete
```

Deletion has a mandatory 24-hour minimum-age grace period to avoid racing uploads or invoice transactions. Objects that are too recent are reported as deferred; missing referenced objects are reported separately and are never deleted. Review the dry-run counts and confirm backups/retention requirements before enabling deletion.


## Read-only agent task API

The authenticated `POST /api/v1/agent/tasks` endpoint runs bounded evidence-gathering plans. It derives tenant scope from the access token and only exposes the allowlisted invoice lookup, purchase-order search, and policy search tools.

Before enabling it, configure and verify one inference provider:

- Ollama: `INFERENCE_PROVIDER=ollama`, `OLLAMA_BASE_URL`, and `OLLAMA_MODEL`.
- vLLM: `INFERENCE_PROVIDER=vllm`, `VLLM_BASE_URL`, `VLLM_MODEL`, and `VLLM_API_KEY`.

The API worker reuses one bounded inference client per process. Concurrency, queue size, queue wait, request timeout, retries, and output tokens are controlled by the `INFERENCE_*` settings documented in `docs/INFERENCE_ENGINEERING.md`. Model servers are separate services and must be deployed, network-restricted, monitored, and capacity-tested independently.

Execution is capped at five read-only tool calls and 30 seconds. Cancellation/deadlines are cooperative between synchronous tool calls; they cannot interrupt a database call already in progress. The endpoint is not a durable job queue. Model output is advisory evidence only and must never replace human review or approval. Do not place secrets, payment credentials, or unrelated personal data in task descriptions.
