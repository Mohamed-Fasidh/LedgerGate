# Security posture and required production controls

## Implemented in this repository
- JWT access tokens and bcrypt password hashing.
- Password length bounded to bcrypt's supported maximum.
- Admin, analyst, and reviewer role checks.
- Tenant filters on business record queries.
- User email uniqueness to keep login unambiguous.
- Upload extension, size, empty-file, and basic PDF-signature checks.
- Randomized server-side upload names.
- Audit events for key actions.
- PostgreSQL row locks around invoice reconciliation, approval, and rejection to serialize competing state changes.
- Pydantic validation for API inputs and agent tool arguments.
- Production startup rejects the default JWT secret and wildcard CORS.

## Required before real enterprise use
1. **Identity:** SSO/OIDC, MFA, email verification, password reset, refresh-token rotation, revocation, brute-force protection, and session/device controls.
2. **Tenant isolation:** PostgreSQL RLS with FORCE ROW LEVEL SECURITY, a non-owner DB runtime role, tenant-isolation integration tests, and tenant-aware background jobs.
3. **Files:** Private encrypted object storage, antivirus/malware scanning, content sniffing, decompression limits, retention/deletion, signed download URLs, and access logging.
4. **Audit:** Append-only/WORM storage, hash chaining/signatures, centralized log retention, and privileged-action monitoring.
5. **Financial controls:** Separation of duties, dual approval thresholds, idempotency keys, approval policy versioning, and reconciliation review. Invoice row locks serialize current decision operations, but do not replace maker-checker controls or idempotency.
6. **Infrastructure:** TLS, secure headers, rate limiting, WAF, secret manager, key rotation, network isolation, DB backups, point-in-time recovery, and restore drills.
7. **Assurance:** Threat model, SAST/DAST, dependency scanning, penetration testing, load tests, privacy/legal review, and incident response plan.

Never put payment credentials, bank passwords, card data, or production secrets in source control. AI outputs must never authorize payments.
