# Security posture and required production controls

## Implemented
- JWT access tokens and bcrypt password hashing.
- Admin, analyst, and reviewer role checks.
- Application tenant filters plus PostgreSQL RLS with FORCE ROW LEVEL SECURITY for tenant-owned tables.
- Authenticated tenant context set on the DB session and reset before pooled connections are released.
- PostgreSQL trigger preventing audit-event updates and deletes.
- Invoice creator attribution, maker-checker enforcement (creator cannot approve/reject their own invoice), and SQLAlchemy optimistic version checks on concurrent invoice updates.
- Tenant-authorized invoice file download from local development storage or a private S3-compatible bucket.
- ClamAV INSTREAM scanning before storage; scanner unavailability fails closed.
- Production startup requires S3-compatible storage, a bucket, a configured malware-scanner host, a strong runtime database password, and the restricted runtime PostgreSQL role.
- Persistent login throttling by hashed email/IP identifiers (10 failed attempts per 15-minute window).
- Tenant-scoped idempotency keys for invoice creation; duplicate same-payload requests replay the original invoice.
- API runtime role bootstrap separates table ownership/migrations from the non-owner, NOBYPASSRLS application connection.
- Optional `Idempotency-Key` on invoice creation is unique per tenant; repeating the same key and payload returns the existing invoice, while reusing the key with a different payload returns HTTP 409.
- Upload extension, size, empty-file, and basic PDF-signature checks; randomized server-side upload names.
- Pydantic validation for API inputs and agent tool arguments.
- Production startup rejects the default JWT secret and wildcard CORS.

## Mandatory before real enterprise use
1. Identity: SSO/OIDC, MFA, email verification, password reset, refresh-token rotation, revocation, persistent login throttling, and session/device controls.
2. Database: create separate migration and runtime roles. Runtime must not own tables and must not have BYPASSRLS. Grant only required privileges and run RLS tests as the exact runtime role.
3. Files: production S3-compatible private object storage and ClamAV scanning are implemented as adapters, but cloud IAM/bucket policy, KMS encryption, retention/deletion, short-lived signed download URLs, and access logging must be configured and independently verified. Content sniffing and PDF parser resource limits still need strengthening.
4. Audit: export append-only events to independently administered WORM storage, cryptographically sign/hash-chain events, centralize log retention, and monitor privileged actions. The DB trigger is defense-in-depth, not WORM.
5. Financial controls: implement configurable dual-approval thresholds (the current code enforces maker-checker only), extend idempotency to uploads and all state-changing endpoints, add approval policy versioning, and maintain reconciliation review. Invoice-create idempotency and optimistic invoice versioning are present.
6. Infrastructure: TLS, secure headers, rate limiting, WAF, secret manager, key rotation, network isolation, DB backups, point-in-time recovery, and restore drills.
7. Assurance: threat model, SAST/DAST, dependency scanning, penetration testing, load tests, privacy/legal review, and incident response plan.

Never put payment credentials, bank passwords, card data, or production secrets in source control. AI outputs must never authorize payments.
