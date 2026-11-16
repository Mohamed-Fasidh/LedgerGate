# LedgerGate build and production-readiness status

Last checked: 2026-11-16

## Current delivery state

- **Stage 1 — core platform:** implemented and merged to `main` in PR #4.
- **Stage 2 — application hardening:** implemented on `hardening/production-readiness-v2` in PR #5. The branch is not yet merged.
- **Latest code CI:** GitHub Actions run [43816875357](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43816875357) completed successfully on application code commit `b6e07246c69d19cbe2e992ab4116942db267ab1e`; backend, frontend, and Docker jobs all passed. A subsequent documentation-only commit is awaiting its own CI run. PR #5 is open and marked ready for review; it has not been merged.
- **Stage 3 — staging deployment:** not yet verified against a provisioned staging environment.
- **Stage 4 — public production deployment:** not yet deployed.
- **Stage 5 — operational launch:** not complete; security assurance, recovery drills, and operational monitoring remain required.

## Implemented in PR #4 and PR #5

### Platform and invoice workflow
- FastAPI API, PostgreSQL, Alembic migrations, Next.js dashboard, Docker Compose, and GitHub Actions.
- Multi-tenant data model, role checks, PostgreSQL FORCE RLS, tenant context setup and pooled-connection cleanup, and a restricted non-owner runtime database role.
- Invoice intake, PDF/text extraction, human confirmation of extracted fields, deterministic invoice/PO reconciliation, review and rejection, and sequential amount-based approval policies.
- Maker-checker controls, approver attribution, invoice policy snapshots, and optimistic concurrency protection.
- Private S3-compatible invoice storage adapter, server-side encryption, tenant-authorized downloads, upload size/type checks, and ClamAV scanning that fails closed when the scanner is unavailable.
- Advisory-only AI/agent explanations and tool allowlists; AI does not approve invoices or initiate payments.

### Security and audit
- Password hashing, short-lived access JWTs, persisted refresh sessions, 30-day refresh expiry, single-use rotation, reuse detection/family revocation, logout, and sign-out-all.
- Host-only HttpOnly refresh cookie, production Secure flag, SameSite=Lax, CSRF cookie/header comparison, allowlisted Origin checks, and frontend removal of refresh tokens from web storage.
- Optional authenticator-app TOTP MFA, encrypted TOTP secrets, replay prevention, failed-code throttling, and one-time hashed recovery codes.
- Tenant-wide idempotency for supported write operations, request fingerprint conflict protection, replayed response handling, and bounded tenant-admin retention maintenance.
- Append-only audit mutation protection, per-tenant SHA-256 event chains, admin chain verification, and signed audit export to a separately provisioned S3 Object Lock bucket with COMPLIANCE retention and AES256 server-side encryption.

## Latest CI result

Run **43816875357** on application code commit `b6e07246c69d19cbe2e992ab4116942db267ab1e`):
- Backend: migrations, restricted runtime-role bootstrap, Python compilation, and pytest — passed.
- Frontend: dependency advisory report, high-severity npm audit gate, and production build — passed.
- Docker: Compose configuration validation and backend/frontend image builds — passed.

All three jobs in run [43816875357](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43816875357) completed successfully: backend, frontend, and Docker. PR #5 remains unmerged pending independent review and explicit merge approval.

CI validates the repository's automated checks. It does not provision cloud resources, validate production secrets/IAM policies, prove disaster recovery, or replace an independent security assessment.

## Remaining work before production

### Application and identity
- SSO/OIDC, verified email, password reset/account recovery, and richer session/device management.
- Registration now has persistent 15-minute limits (5 per normalized email and 20 per source IP), keyed HMAC identifiers, PostgreSQL advisory-lock serialization, bounded stale-row cleanup, and regression/concurrency coverage. Continue reviewing other unauthenticated account lifecycle endpoints and extend idempotency to any remaining or future non-repeatable writes.
- Validate MFA recovery, administrator-assisted recovery procedures, and session revocation behavior with real multi-user scenarios.

### Cloud and operations
- Provision staging and production infrastructure; configure managed PostgreSQL, private networking, TLS/reverse proxy, WAF/rate limits, secret manager, workload IAM, and alerting.
- Provision a versioned S3 Object Lock bucket and independently managed signing/encryption keys. The export implementation is present, but bucket creation, IAM, key custody/rotation, retention policy approval, and an end-to-end cloud export must be verified in the target account.
- Configure customer-managed KMS encryption if required; current adapters request AES256/SSE-S3, not SSE-KMS.
- Configure ClamAV signature updates and resource limits, object lifecycle/access logging, backups/PITR, and tested restore procedures.
- Run migrations as a one-shot release job for multi-replica deployments; do not let every API replica race to migrate.
- Configure the frontend and API on the same public hostname (for example, route `/api` through the reverse proxy) so host-only CSRF cookies work as designed.

### Assurance and launch
- Independent threat-model review, SAST/DAST, penetration testing, load testing, privacy/legal review, and incident-response tabletop.
- Verify tenant isolation using the exact production runtime role and cross-tenant negative tests.
- Run backup restore, audit export verification, key rotation, alert delivery, and incident response drills.
- Complete staging UAT and obtain explicit operational/security sign-off before production traffic.

## Scope boundary

LedgerGate is an invoice verification and human-approval workflow platform. It does **not** initiate payments, hold funds, or autonomously approve financial transactions. A successful CI run is not a production certification.
