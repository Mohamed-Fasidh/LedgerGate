# Security posture and required production controls

## Implemented in this repository
- JWT access tokens and bcrypt password hashing.
- Password length bounded to bcrypt's supported maximum.
- Admin, analyst, and reviewer role checks.
- Tenant filters on business record queries.
- User email uniqueness to keep login unambiguous.
- Login throttling keyed by a SHA-256 digest of normalized email, serialized with PostgreSQL transaction advisory locks; repeated failures trigger a temporary account lock.
- Password-reset requests use a uniform response to reduce account enumeration; reset tokens are random, stored as SHA-256 hashes, expire after 15 minutes, and are single-use.
- Password reset establishes the token's tenant context before querying or revoking FORCE-RLS-protected authentication sessions.
- Upload extension, byte-size, empty-file, PDF-signature, strict UTF-8 text, and binary-content checks.
- Restrictive per-tenant local upload directory/file permissions for development, private S3-compatible storage for production, tenant-scoped opaque object keys, S3 SSE-S3 AES-256 upload requests, short-lived presigned downloads, and a configurable PDF page-count limit; encrypted PDFs are rejected.
- Randomized server-side upload names.
- Tenant-scoped audit events with SHA-256 hash chaining, PostgreSQL transaction advisory locks for serialized writers, and a database trigger that rejects UPDATE/DELETE.
- PostgreSQL row locks around invoice reconciliation, approval, and rejection to serialize competing state changes.
- Optional database-backed `Idempotency-Key` support for invoice approval/rejection, scoped to tenant and authenticated user, with request-fingerprint conflict detection, replayed response bodies, PostgreSQL advisory-lock serialization, and 24-hour expiry.
- Maker-checker enforcement prevents the recorded invoice creator from approving or rejecting their own invoice; creator and decision-maker IDs are persisted.
- Tenant approval policies are append-only, versioned per workspace, and admin-managed. Invoices snapshot the applicable policy version, threshold, and required approval count; later versions do not change in-flight invoices. Approval thresholds can be configured independently in each supported invoice currency; no implicit FX conversion is performed.
- Configurable high-value INR invoices require two distinct reviewer accounts. Each approval is persisted in a tenant-isolated table; the invoice remains in review until the second independent approval. Default threshold: INR 100,000 (`DUAL_APPROVAL_THRESHOLD_INR`). The invoice list/detail API and dashboard show progress, and a tenant-scoped endpoint returns reviewer IDs and timestamps.
- Pydantic validation for API inputs and agent tool arguments.
- API responses include anti-sniffing, anti-framing, referrer, permissions, and opener headers; API routes receive a restrictive content security policy and authentication responses are marked no-store. HSTS is enabled by the API in production.
- Frontend responses include anti-sniffing, anti-framing, referrer, permissions, and opener headers.
- Production startup rejects the default JWT secret and wildcard CORS.

## Important authentication data-access boundary
Login and password-reset request flows must locate a user before the tenant ID is known. Consequently, `users`, `login_throttles`, and `password_reset_tokens` do not currently have tenant RLS policies. This is an explicit pre-authentication exception, not equivalent to tenant isolation. The current runtime role has broad SELECT/INSERT/UPDATE/DELETE grants on all application tables, so a compromised application database credential could read or modify authentication records across tenants. Do not claim complete database-level tenant isolation for these tables.

Before production, replace broad authentication-table access with a narrowly scoped and reviewed authentication data-access boundary (for example, security-definer routines with fixed safe search paths and strict input/output contracts, or a separate credential/pool). Add PostgreSQL integration tests that execute login, registration, refresh, reset request, and reset confirmation using the actual restricted runtime credentials before reducing grants. Avoid adding a simplistic tenant policy that prevents pre-authentication account discovery.

The current email-keyed throttle can also be abused to temporarily lock out a victim account. Add layered source/network throttling and abuse monitoring, and design lockout recovery carefully. Trust forwarded client IP headers only behind explicitly trusted proxies. Password-reset delivery needs per-source and per-account quotas, provider limits, and monitoring to mitigate email flooding.

## Required before real enterprise use
1. **Identity:** TOTP MFA, password-reset tokens delivered through configured SMTP, refresh-token rotation/revocation, brute-force protection, and session controls are implemented. SSO/OIDC, email verification, and device metadata/notifications remain outstanding.
2. **Tenant isolation:** PostgreSQL RLS with FORCE ROW LEVEL SECURITY protects tenant-owned business tables and auth sessions. Pre-authentication identity/recovery tables remain an explicit exception as described above. A non-owner DB runtime role and tenant-isolation integration tests exist, but auth-table privilege separation and tenant-aware background jobs remain required.
3. **Files:** Production configuration requires S3-compatible storage. The application requests SSE-S3 AES-256, uses tenant-scoped opaque object keys, and creates short-lived presigned downloads after tenant authorization. Unit tests mock S3; a live bucket integration and cloud-policy review are still required. Malware scanning, robust content sniffing, parser sandboxing and CPU/memory/decompression limits, approved retention/deletion, orphan reconciliation, and access logging/alerting remain required.
4. **Audit:** Current database audit rows are append-only under ordinary SQL UPDATE/DELETE and hash-chained per tenant. External WORM storage, independent chain checkpoints/signatures, centralized log retention, alerting, and privileged/superuser tamper monitoring are still required; database administrators can disable triggers or rewrite the chain.
5. **Financial controls:** Maker-checker and configurable two-reviewer approval for high-value INR invoices are implemented. The default threshold is INR 100,000 and can be changed with `DUAL_APPROVAL_THRESHOLD_INR`. Foreign-currency invoices use single-reviewer approval until an administrator creates an explicit policy denominated in that currency. No FX conversion is performed. Idempotency currently covers approve/reject only; broader mutation idempotency and independent reconciliation review remain required.
6. **Infrastructure:** TLS termination, WAF, secret manager, key rotation, network isolation, DB backups, point-in-time recovery, and restore drills remain deployment responsibilities.
7. **Assurance:** Threat model, SAST/DAST, dependency scanning, penetration testing, load tests, privacy/legal review, and incident response plan.

Never put payment credentials, bank passwords, card data, or production secrets in source control. AI outputs must never authorize payments.
