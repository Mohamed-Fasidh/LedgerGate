# LedgerGate build and production-readiness status

Last checked: 2026-11-16

## Current delivery state

- **Stage 1 — core platform:** implemented and merged to `main` in PR #4.
- **Stage 2 — application hardening:** implemented on `hardening/production-readiness-v2` in PR #5. The branch is not yet merged.
- **Latest fully passing CI:** run [43818537960](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43818537960) passed backend, frontend, and Docker on commit `14608bfe4a5c0aed888118a443a1043d5c34f236`. New MFA session-revocation changes and their tests are on PR #5 head `41c510b797148a5673b47f6dcbc82ce32d9685b8`; CI run [43819401259](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43819401259) is pending. PR #5 is open and unmerged.
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
- Optional authenticator-app TOTP MFA, encrypted TOTP secrets, replay prevention, failed-code throttling, and one-time hashed recovery codes. Disabling MFA requires the current password plus fresh TOTP or an unused recovery code, revokes all active refresh sessions, clears auth cookies, and records the revoked-session count in the audit event.
- Tenant-wide idempotency for supported write operations, request fingerprint conflict protection, replayed response handling, and bounded tenant-admin retention maintenance.
- Append-only audit mutation protection, per-tenant SHA-256 event chains, admin chain verification, and signed audit export to a separately provisioned S3 Object Lock bucket with COMPLIANCE retention and AES256 server-side encryption.

## Latest CI result

The last fully successful run is **43818537960** on commit `14608bfe4a5c0aed888118a443a1043d5c34f236`:
- Backend: migrations through `0017_login_throttle_indexes`, restricted runtime-role bootstrap, Python compilation, and pytest — passed.
- Frontend: dependency advisory report, high-severity npm audit gate, and production build — passed.
- Docker: Compose configuration validation and backend/frontend image builds — passed.

The current PR head is `41c510b797148a5673b47f6dcbc82ce32d9685b8`. Its CI run [43819401259](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43819401259) is pending and must pass before these newest MFA session-revocation changes can be considered CI-verified. PR #5 remains unmerged pending independent review and explicit merge approval.

CI validates automated repository checks. It does not provision cloud resources, validate production secrets/IAM policies, prove disaster recovery, or replace an independent security assessment.

## Remaining work before production

### Application and identity
- SSO/OIDC, verified email, password reset/account recovery, and richer session/device management.
- Registration now has persistent 15-minute limits (5 per normalized email and 20 per source IP), keyed HMAC identifiers, PostgreSQL advisory-lock serialization, bounded stale-row cleanup, and regression/concurrency coverage. Login throttling now uses keyed HMAC identifiers, cross-replica PostgreSQL advisory locks, a 10-failure/15-minute threshold, bounded cleanup, and composite query indexes; PostgreSQL concurrency coverage verifies simultaneous requests are serialized. Continue reviewing other unauthenticated account lifecycle endpoints and extend idempotency to any remaining or future non-repeatable writes.
- Validate MFA recovery, administrator-assisted recovery procedures, and MFA-disable/session-revocation behavior with real multi-user scenarios.

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
