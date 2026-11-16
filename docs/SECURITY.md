# Security posture and required production controls

## Implemented
- Short-lived JWT access tokens, persisted 30-day refresh sessions, single-use refresh-token rotation, reuse detection with token-family revocation, logout revocation, and bcrypt password hashing.
- Browser refresh credentials are held in host-only `HttpOnly` cookies scoped to `/api/v1/auth`, with `Secure` enabled in production and `SameSite=Lax`. A separate readable CSRF cookie must match the `X-CSRF-Token` header, and refresh/logout require an allowlisted `Origin`. Login and registration reject untrusted browser origins. The frontend sends cookies with credentialed requests and does not store refresh tokens in web storage. Because the CSRF cookie is host-only and read by the browser client, production should serve the UI and API on the same hostname (for example, reverse-proxy `/api` to the backend); do not weaken cookie attributes to work around a cross-site deployment.
- Admin, analyst, and reviewer role checks.
- Application tenant filters plus PostgreSQL RLS with FORCE ROW LEVEL SECURITY for tenant-owned tables.
- Authenticated tenant context set on the DB session and reset before pooled connections are released.
- PostgreSQL trigger preventing audit-event updates and deletes; per-tenant SHA-256 hash chaining with an admin verification endpoint (tamper-evident within the database, not a substitute for external WORM).
- Invoice creator attribution, maker-checker enforcement, configurable amount-based sequential approval stages, per-stage role requirements, unique approver records, invoice-level policy snapshots, and SQLAlchemy optimistic version checks on concurrent decisions.
- Tenant-authorized invoice file download from local development storage or a private S3-compatible bucket.
- ClamAV INSTREAM scanning before storage; scanner unavailability fails closed.
- Production startup requires S3-compatible storage, a bucket, a configured malware-scanner host, a strong runtime database password, and the restricted runtime PostgreSQL role.
- Persistent login throttling by hashed email/IP identifiers (10 failed attempts per 15-minute window).
- Tenant-wide idempotency records reserve keys across supported state-changing operations, including invoice creation and upload. Request fingerprints prevent a key from being replayed with different content or across operations; migration 0012 backfills legacy invoice keys and fails safely on historical collisions.
- API runtime role bootstrap separates table ownership/migrations from the non-owner, NOBYPASSRLS application connection.
- Tenant administrators can remove idempotency response records older than a configurable 30-3,650-day retention window in bounded batches. Cleanup is tenant-filtered, respects PostgreSQL RLS, and writes an audit event; the endpoint is designed for a periodic authenticated maintenance process.
- Optional `Idempotency-Key` on invoice creation and upload is reserved in the same tenant-wide idempotency table used by other write operations; identical retries return the original response, while different payloads or cross-operation reuse return HTTP 409. Concurrent upload races clean up the losing uncommitted object.
- Configurable approval policies select by invoice amount and define sequential role-based stages; each invoice snapshots its approval plan, requires distinct approvers, and is only marked approved after every stage succeeds.
- Upload extension, size, empty-file, and basic PDF-signature checks; randomized server-side upload names.
- Pydantic validation for API inputs and agent tool arguments.
- Production startup rejects the default JWT secret and wildcard CORS.

## Mandatory before real enterprise use
1. Identity: SSO/OIDC, MFA, email verification, password reset and account recovery, persistent login throttling, refresh-session expiry/cleanup, and richer session/device management. Validate cookie behavior behind the production HTTPS proxy and keep frontend/API origins explicitly allowlisted.
2. Database: create separate migration and runtime roles. Runtime must not own tables and must not have BYPASSRLS. Grant only required privileges and run RLS tests as the exact runtime role.
3. Files: production S3-compatible private object storage and ClamAV scanning are implemented as adapters, but cloud IAM/bucket policy, KMS encryption, retention/deletion, short-lived signed download URLs, and access logging must be configured and independently verified. Content sniffing and PDF parser resource limits still need strengthening.
4. Audit: export append-only events to independently administered WORM storage, cryptographically sign/hash-chain events, centralize log retention, and monitor privileged actions. Per-tenant SHA-256 chaining and an admin verification endpoint are implemented; external WORM export and signing keys remain outstanding. The DB trigger is defense-in-depth, not WORM.
5. Financial controls: implement configurable dual-approval thresholds (the current code enforces maker-checker only), extend idempotency to all remaining state-changing endpoints, add approval policy versioning, and maintain reconciliation review. Invoice-create idempotency and optimistic invoice versioning are present.
6. Infrastructure: TLS, secure headers, rate limiting, WAF, secret manager, key rotation, network isolation, DB backups, point-in-time recovery, and restore drills.
7. Assurance: threat model, SAST/DAST, dependency scanning, penetration testing, load tests, privacy/legal review, and incident response plan.

Never put payment credentials, bank passwords, card data, or production secrets in source control. AI outputs must never authorize payments.
