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

## Database backup example
For local Docker development:
```bash
docker compose exec -T db pg_dump -U ledgergate ledgergate > ledgergate-backup.sql
```
Restore drills must be performed in a separate environment before relying on backups.

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
- Restrict keyring access, log verification operations without logging key material, maintain protected offline recovery copies, and document emergency revocation/re-signing procedures. Do not place historical secrets in source control or ordinary application logs.
- The application currently accepts one active signing key through environment configuration. Historical key retention and independent verification-keyring storage remain operator responsibilities; this code does not implement a managed KMS keyring or automatic rotation.
