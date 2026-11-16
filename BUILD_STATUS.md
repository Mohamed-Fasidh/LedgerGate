# LedgerGate build and production-readiness status

Last checked: 2026-11-16

## Current delivery state

- **Stage 1 — core platform:** implemented and merged to `main` in PR #4.
- **Stage 2 — application hardening:** implemented on `hardening/production-readiness-v2` in PR #5. The branch is not yet merged.
- **Latest fully passing CI:** run [43837754285](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43837754285) passed backend, frontend, and Docker on PR #5 head `3fe36c4fa2a6b69edbc28e75d6b3e82d8946119e`. PR #5 is open and unmerged.
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

The latest fully successful run is **43837754285** on commit `3fe36c4fa2a6b69edbc28e75d6b3e82d8946119e` (backend, frontend, Docker all passed):
- Backend: migrations through `0017_login_throttle_indexes`, restricted runtime-role bootstrap, Python compilation, and pytest — passed.
- Frontend: dependency advisory report, high-severity npm audit gate, and production build — passed.
- Docker: operational-script syntax and regression checks, Compose configuration validation, and backend/frontend image builds — passed.

Subsequent changes added tenant-member deactivation, audit and approval-policy administration screens, row-locked idempotent approval-policy deactivation, and regression tests for that operation. PR #5 remains unmerged pending independent review and explicit merge approval.

CI validates automated repository checks. It does not provision cloud resources, validate production secrets/IAM policies, prove disaster recovery, or replace an independent security assessment.


## Additional audit-export and recovery hardening (PR #5)

- Audit export attempts now use a unique UUID in the signed document and S3 object key, including when the same event range is exported again. This avoids reusing an Object Lock key on retries.
- PostgreSQL backup publication now writes and validates the archive first, publishes its checksum sidecar atomically last, and removes incomplete outputs on failure.
- Paginated audit verification regression fixtures now implement the SQLAlchemy limit operation used by the production query path, including valid, empty, and corrupted-chain cases.
- Audit-chain verification now reads bounded pages (default 1,000, maximum 10,000) rather than loading the tenant's full history into application memory. Admin verification also takes the shared tenant writer lock so the multi-page result represents a stable chain.
- Immutable audit export now acquires the same PostgreSQL transaction-scoped tenant advisory lock as audit writers before verification and holds it through export commit. This prevents a delayed writer with a lower global sequence ID from committing behind an advanced export cursor. Added PostgreSQL/non-PostgreSQL lock tests and endpoint lock-order regression coverage.
- Audit export queries are now bounded by the exact event ID at the verified chain head; concurrent later appends are deferred until the next fresh verification. Regression tests cover the verified head, empty chains, and cursor-at-head behavior.
- Added an offline `python -m scripts.verify_audit_export` CLI that validates downloaded exports against a protected operator keyring file, enforces restrictive POSIX keyring permissions, and returns automation-friendly exit codes. Added valid, tampered, insecure-permission, and malformed-keyring tests.
- Added an independent export verifier that checks the signing-key ID/HMAC, export format and metadata, tenant consistency, ascending event IDs, event count, first/last event metadata, and every event's previous-hash and calculated hash.
- Added regression coverage for valid exports, tampered event hashes and links, cross-tenant events, inconsistent metadata, unknown keys, and malformed verifier inputs.
- Documented reconciliation for immutable S3 objects that may be left unreferenced if the database commit fails after a successful object write.
- Added PostgreSQL backup and disposable restore-drill scripts with archive validation and SHA-256 checksums. CI checks their Bash syntax; a real restore drill still needs to be executed against an isolated database environment.
- CI run [43825372024](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43825372024) passed backend, frontend, and Docker for the backup/restore scripts and operations documentation. Later audit-verifier changes are awaiting their own CI result.

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

- Added tenant-admin role changes for active non-admin members (analyst/reviewer only), with tenant scoping, row locking, refresh-session revocation, audit metadata, and regression coverage. CI verification pending for latest branch head.

- Added self-service refresh-session inventory and per-session revocation, scoped to the authenticated tenant/user, with row locking, audit events, minimal response fields, and regression coverage. Latest CI verification pending.

- PostgreSQL backup creation now uses collision-resistant temporary and final filenames so overlapping backup jobs cannot overwrite one another. Restore drills use unique disposable database names and verify a populated Alembic version plus the core users/invoices tables before reporting success.

- CI now runs deterministic fake-Docker regression suites for both PostgreSQL backup and restore-drill scripts. Coverage includes concurrent backup naming, checksum/archive validation, failed dump/restore cleanup, schema verification, and opt-in retention of a disposable restored database. These tests do not claim an actual infrastructure-level recovery drill.

- Production API image startup now launches Uvicorn only; privileged Alembic migrations and runtime-role bootstrap are separate one-shot release tasks. Local Compose retains an explicit developer-only bootstrap command. CI guards this separation to prevent multi-replica schema migration races.


- Container hardening update: API image now runs as dedicated UID/GID 10001 with upload-volume ownership initialized for that identity; frontend image runs as the unprivileged `node` user. Local Compose now checks API and frontend health and gates frontend startup on API health. Added a CI regression test for these Dockerfile/Compose invariants. This health endpoint currently checks HTTP process responsiveness only; dependency-aware readiness and real orchestrator probes remain deployment validation tasks.

- API readiness hardening: added `/ready`, which checks PostgreSQL with `SELECT 1` and verifies configured ClamAV TCP reachability; returns 503 when a required dependency is unavailable. `/health` remains a lightweight liveness check. Compose now gates frontend startup on dependency-aware API readiness. Regression tests cover healthy dependencies, database failure, scanner failure, and development without a configured scanner. S3 connectivity/permissions remain an integration-test gate.

- Additional runtime restrictions: local Compose now applies `no-new-privileges` and drops all Linux capabilities for API and frontend containers; CI asserts both services retain these settings.


## ClamAV protocol response hardening (PR #5)

- Upload scanning now caps the ClamAV daemon's status response at 4 KiB. An oversized response is rejected as a scanner failure instead of being accumulated without a bound in API memory.
- Added regression coverage for a valid NUL-terminated response, an oversized response, and an unterminated response. All malformed scanner responses fail closed.
- Latest previously verified full CI run: [43832704393](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43832704393), all three jobs passed before this incremental change. This change requires its own CI run.


## Document extraction resource limits (PR #5)

- Text and PDF extraction now enforce a shared 2-million-character output cap; TXT reads are bounded to one character beyond that cap before rejection.
- PDF page count remains capped at 50. OCR now estimates rendered page pixels before allocating images and rejects pages above 12 million pixels or documents above 48 million total OCR pixels.
- Added regression tests for oversized TXT/parser input and OCR page-dimension limits. OCR remains optional when its Python dependencies are unavailable; extraction candidates still require human confirmation.
- Latest fully passing CI before this change: [43834026551](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43834026551).


## Verification refresh — 2026-11-16

- PR #5 head `0a2608d6a033989053741367c20b209ff9871e70` passed GitHub Actions run [43836611143](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43836611143): backend migrations/runtime-role bootstrap/compilation/pytest, frontend dependency audit and production build, and Docker operational-script checks/Compose validation/image builds all passed.
- This is CI evidence for the PR branch only. PR #5 remains open and unmerged; `main` does not yet contain these changes.
- Several earlier runs were automatically cancelled when newer commits superseded them. One earlier run failed ([43833599492](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43833599492)); the current PR head subsequently passed all three jobs.
- The repository is not production-complete. Staging deployment, cloud resource provisioning and end-to-end IAM/KMS/Object Lock validation, real backup/restore and audit-export drills, identity lifecycle features (SSO/OIDC, verified email, password recovery), independent security/load testing, and operational/legal sign-off remain outstanding.

## Latest security verification — 2026-11-16

- GitHub Actions run [43840701587](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43840701587) passed backend, frontend, and Docker jobs on commit `a540cb16acb04e0787181eecccb1eba0c8637ca3`.
- S3 invoice download reads are bounded to the configured upload limit plus one byte; oversized stored objects are rejected and response streams are closed. Regression tests cover both oversized and valid bounded downloads.
- Invoice reconciliation is restricted to administrator and analyst roles. Reviewers cannot invoke this processing action; a regression test checks the role boundary.
- PR #5 remains open and unmerged pending independent review and production acceptance. Passing CI is not production certification.
