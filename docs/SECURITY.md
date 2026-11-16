# Security posture and required production controls

## Implemented
- JWT access tokens and bcrypt password hashing.
- Admin, analyst, and reviewer role checks.
- Application tenant filters plus PostgreSQL RLS with FORCE ROW LEVEL SECURITY for tenant-owned tables.
- Authenticated tenant context set on the DB session and reset before pooled connections are released.
- PostgreSQL trigger preventing audit-event updates and deletes.
- Invoice creator attribution, maker-checker enforcement (creator cannot approve/reject their own invoice), and SQLAlchemy optimistic version checks on concurrent invoice updates.
- Tenant-authorized invoice file download; locally stored uploads use randomized names and restrictive POSIX permissions where supported.
- Upload extension, size, empty-file, and basic PDF-signature checks; randomized server-side upload names.
- Pydantic validation for API inputs and agent tool arguments.
- Production startup rejects the default JWT secret and wildcard CORS.

## Mandatory before real enterprise use
1. Identity: SSO/OIDC, MFA, email verification, password reset, refresh-token rotation, revocation, brute-force protection, and session/device controls.
2. Database: create separate migration and runtime roles. Runtime must not own tables and must not have BYPASSRLS. Grant only required privileges and run RLS tests as the exact runtime role.
3. Files: current local filesystem storage is not a production object store. Add private encrypted object storage, antivirus/malware scanning, robust content sniffing, decompression limits, retention/deletion, short-lived signed download URLs, and access logging.
4. Audit: export append-only events to independently administered WORM storage, cryptographically sign/hash-chain events, centralize log retention, and monitor privileged actions. The DB trigger is defense-in-depth, not WORM.
5. Financial controls: implement configurable dual-approval thresholds (the current code enforces maker-checker only), idempotency keys, approval policy versioning, and reconciliation review. Optimistic invoice versioning is present.
6. Infrastructure: TLS, secure headers, rate limiting, WAF, secret manager, key rotation, network isolation, DB backups, point-in-time recovery, and restore drills.
7. Assurance: threat model, SAST/DAST, dependency scanning, penetration testing, load tests, privacy/legal review, and incident response plan.

Never put payment credentials, bank passwords, card data, or production secrets in source control. AI outputs must never authorize payments.
