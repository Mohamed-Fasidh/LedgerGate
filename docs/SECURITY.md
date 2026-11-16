# Security posture and production controls

## Implemented application controls

- Password hashing, short-lived JWT access tokens, persisted refresh sessions, 30-day expiry, single-use refresh-token rotation, token reuse detection with family revocation, logout revocation, and sign-out-all-devices.
- Browser refresh credentials use a host-only `HttpOnly` cookie scoped to `/api/v1/auth`; production enables `Secure` and cookies use `SameSite=Lax`. A separate readable CSRF cookie must match the `X-CSRF-Token` header, and refresh/logout require an allowlisted `Origin`. Login and registration reject untrusted browser origins. The frontend does not store refresh tokens in web storage.
- Optional authenticator-app TOTP MFA, Fernet-encrypted TOTP secrets, atomic replay prevention, failed-code throttling, and ten one-time recovery codes stored only as hashes. Recovery codes are shown only immediately after enrollment.
- Application tenant filters plus PostgreSQL RLS with FORCE ROW LEVEL SECURITY for tenant-owned tables; tenant context is set on the DB session and reset before pooled connections are released.
- Separate migration-owner and non-owner `ledgergate_runtime` database connections; the runtime role is provisioned without superuser or BYPASSRLS privileges.
- Append-only PostgreSQL audit mutation protection and per-tenant SHA-256 event chaining with an admin verification endpoint.
- Signed HMAC-SHA256 audit export batches to a separately provisioned S3 Object Lock bucket. Export verifies the tenant event chain, bucket versioning and Object Lock configuration, and writes objects with COMPLIANCE retention and AES256 server-side encryption.
- Invoice creator attribution, maker-checker enforcement, configurable amount-based sequential approval stages, per-stage role requirements, distinct approver records, invoice-level policy snapshots, and optimistic concurrency checks.
- Tenant-wide idempotency for supported state-changing operations. Request fingerprints reject mismatched or cross-operation key reuse; successful retries can replay the stored response.
- Bounded tenant-admin cleanup for expired idempotency records and expired refresh sessions. Cleanup is tenant-filtered, RLS-protected, and audit logged; unexpired sessions are not selected.
- Private S3-compatible invoice storage adapter, tenant-authorized downloads, upload limits/basic file checks, randomized storage names, and ClamAV INSTREAM scanning before storage. Scanner unavailability fails closed.
- Persistent login throttling using hashed email/IP identifiers.
- Pydantic validation for API inputs and agent tool arguments. AI output is advisory; only authorized human reviewers can finalize invoice decisions.

## Browser deployment constraint

Serve the dashboard and API on the **same hostname** in production, preferably with a reverse proxy routing `/api` to FastAPI. The refresh cookie is host-only and the frontend reads the CSRF cookie to echo its value in a request header. Do not weaken cookie protections to compensate for a cross-site frontend/API layout. Configure exact trusted origins and HTTPS.

## Required before real enterprise use

1. **Identity and account lifecycle:** implement and verify SSO/OIDC, email verification, password reset/account recovery, and richer session/device management. Exercise MFA recovery and administrator-assisted recovery procedures. Review public registration abuse controls.
2. **Database:** create separate migration and runtime roles. Runtime must not own tables and must not have BYPASSRLS. Grant only required privileges and run tenant-isolation tests as the exact production runtime role.
3. **Object storage:** provision private buckets and least-privilege workload IAM. Configure public-access blocking, versioning, encryption, retention/lifecycle, TLS-only policies, access logging, and backups. The code currently requests AES256/SSE-S3; configure and test SSE-KMS if customer-managed keys are required. Validate malware signature updates and scanner resource limits.
4. **Immutable audit:** provision a separately administered S3 Object Lock bucket with versioning and approved COMPLIANCE retention. Protect signing keys in a secrets manager/KMS-backed process, document key custody/rotation, and run end-to-end export and signature-verification drills. The code implements export but cannot provision or independently attest to the cloud account.
5. **Application assurance:** extend idempotency to remaining/future non-repeatable writes, test concurrent retries and account lifecycle flows, strengthen file content sniffing/parser resource limits, and review approval-policy lifecycle/version governance.
6. **Infrastructure:** TLS, secure headers, trusted proxy settings, WAF/rate limiting, secrets manager, key rotation, private networking, monitoring/alerting, database backups/PITR, and restore drills.
7. **Independent assurance:** threat model, SAST/DAST, penetration testing, load tests, privacy/legal review, incident response plan, and operational sign-off.

Never commit production secrets. Do not store card data, bank credentials, or payment secrets in LedgerGate. LedgerGate does not initiate payments. Passing CI does not certify the platform for regulated financial production use.
