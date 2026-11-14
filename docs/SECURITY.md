# Security posture and required production controls

## Implemented in this repository
- JWT access tokens and bcrypt password hashing.
- Password length bounded to bcrypt's supported maximum.
- Admin, analyst, and reviewer role checks.
- Tenant filters on business record queries.
- User email uniqueness to keep login unambiguous.
- Upload extension, byte-size, empty-file, PDF-signature, strict UTF-8 text, and binary-content checks.
- Restrictive per-tenant local upload directory/file permissions and a configurable PDF page-count limit; encrypted PDFs are rejected.
- Randomized server-side upload names.
- Tenant-scoped audit events with SHA-256 hash chaining, PostgreSQL transaction advisory locks for serialized writers, and a database trigger that rejects UPDATE/DELETE.
- PostgreSQL row locks around invoice reconciliation, approval, and rejection to serialize competing state changes.
- Optional database-backed `Idempotency-Key` support for invoice approval/rejection, scoped to tenant and authenticated user, with request-fingerprint conflict detection, replayed response bodies, PostgreSQL advisory-lock serialization, and 24-hour expiry.
- Maker-checker enforcement prevents the recorded invoice creator from approving or rejecting their own invoice; creator and decision-maker IDs are persisted.
- Tenant approval policies are append-only, versioned per workspace, and admin-managed. Invoices snapshot the applicable policy version, threshold, and required approval count; later versions do not change in-flight invoices. INR policies only are supported currently.\n- Configurable high-value INR invoices require two distinct reviewer accounts. Each approval is persisted in a tenant-isolated table; the invoice remains in review until the second independent approval. Default threshold: INR 100,000 (`DUAL_APPROVAL_THRESHOLD_INR`). The invoice list/detail API and dashboard show progress, and a tenant-scoped endpoint returns reviewer IDs and timestamps.
- Pydantic validation for API inputs and agent tool arguments.
- Production startup rejects the default JWT secret and wildcard CORS.

## Required before real enterprise use
1. **Identity:** SSO/OIDC, MFA, email verification, password reset, refresh-token rotation, revocation, brute-force protection, and session/device controls.
2. **Tenant isolation:** PostgreSQL RLS with FORCE ROW LEVEL SECURITY, a non-owner DB runtime role, tenant-isolation integration tests, and tenant-aware background jobs.
3. **Files:** Current uploads still use local disk and are not malware-scanned. Before enterprise use, move to private encrypted object storage, add antivirus/malware scanning, robust content sniffing, parser sandboxing and CPU/memory/decompression limits, retention/deletion, signed download URLs, and access logging.
4. **Audit:** Current database audit rows are append-only under ordinary SQL UPDATE/DELETE and hash-chained per tenant. External WORM storage, independent chain checkpoints/signatures, centralized log retention, alerting, and privileged/superuser tamper monitoring are still required; database administrators can disable triggers or rewrite the chain.
5. **Financial controls:** Maker-checker and configurable two-reviewer approval for high-value INR invoices are implemented. The default threshold is INR 100,000 and can be changed with `DUAL_APPROVAL_THRESHOLD_INR`. Foreign-currency invoices currently use single-reviewer approval; FX conversion and foreign-currency policy support are not implemented. Idempotency currently covers approve/reject only; broader mutation idempotency and independent reconciliation review remain required.
6. **Infrastructure:** TLS, secure headers, rate limiting, WAF, secret manager, key rotation, network isolation, DB backups, point-in-time recovery, and restore drills.
7. **Assurance:** Threat model, SAST/DAST, dependency scanning, penetration testing, load tests, privacy/legal review, and incident response plan.

Never put payment credentials, bank passwords, card data, or production secrets in source control. AI outputs must never authorize payments.
