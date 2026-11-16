# LedgerGate build and production-readiness status

Last checked: 2026-11-16

## Current verified state — main branch

- PR #4 and PR #5 are both **merged** into `main`. PR #5 merge commit: `f50bdf6b23c9d882e8cf67ef45e2024971f6bdb3`.
- The CI workflow was updated to run on pull requests, pushes to `main`, and manual dispatch.
- The first post-merge main-branch verification completed successfully: [GitHub Actions run 43879483600](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43879483600), commit `20a25b663415030b1ea8e5bcce1e7f9eea2c1f9d`.
- **Backend passed:** dependency installation, PostgreSQL migrations, restricted runtime-role bootstrap, Python compilation, and the complete pytest suite.
- **Frontend passed:** dependency advisory reporting, high-severity npm audit gate, and Next.js production build.
- **Docker passed:** operational-script validation, cleanup safety tests, production startup/migration guards, PostgreSQL backup/restore regression checks, non-root container checks, Compose validation, and backend/frontend image builds.
- This is repository CI verification. It does not prove deployment to a real staging/production environment, live SMTP delivery, cloud IAM/Object Lock/KMS configuration, real restore recovery, or independent security acceptance.

## Current delivery state

- **Stage 1 — core platform:** implemented and merged in PR #4.
- **Stage 2 — application hardening:** implemented and merged in PR #5.
- **Stage 3 — main-branch CI:** post-merge backend, frontend, and Docker jobs passed.
- **Stage 4 — staging deployment:** not yet verified against a provisioned staging environment.
- **Stage 5 — production launch:** not complete; external infrastructure, security assurance, recovery drills, UAT, and operational sign-off remain required.

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
- Email verification and password recovery with cryptographically random single-use tokens stored only as SHA-256 hashes. New registrations remain unable to sign in until their email is verified; verification links expire after 24 hours, reset links after 30 minutes. Generic responses, per-email/IP throttling, SMTP STARTTLS, frontend verification/resend/reset screens, MFA challenge on reset, audit logging, and immediate session revocation are implemented.
- Optional authenticator-app TOTP MFA, encrypted TOTP secrets, replay prevention, failed-code throttling, and one-time hashed recovery codes. Disabling MFA requires the current password plus fresh TOTP or an unused recovery code, revokes all active refresh sessions, clears auth cookies, and records the revoked-session count in the audit event.
- Tenant-wide idempotency for supported write operations, request fingerprint conflict protection, replayed response handling, and bounded tenant-admin retention maintenance.
- Append-only audit mutation protection, per-tenant SHA-256 event chains, admin chain verification, and signed audit export to a separately provisioned S3 Object Lock bucket with COMPLIANCE retention and AES256 server-side encryption.

## Previous CI result details (superseded by run 43866324051)

Run **43864520264** on commit `0d8c41d58ac8f72aea523699656dd11e81cb5863` (backend, frontend, and Docker all passed):
- Backend: database migrations, restricted runtime-role bootstrap, Python compilation, and the full pytest suite — passed. This includes the corrected UTF-8 password byte-limit boundary tests.
- Frontend: dependency advisory reporting, high-severity dependency audit gate, and production build — passed.
- Docker: operational-script and cleanup-safety tests, production startup and one-shot migration guards, PostgreSQL backup/restore checks, non-root container and health-check tests, Compose validation, and backend/frontend image builds — passed.

The passing run verifies this exact PR #5 head. It does not certify cloud configuration, production deployment, disaster recovery, or independent security assurance. Historical note: PR #5 was subsequently merged into main; see Current verified state at the top of this document.

## Remaining work before production

### Application and identity
- SSO/OIDC, durable email-delivery outbox/retries, and richer session/device management. Email verification and password reset are implemented; external SMTP delivery, real mailbox receipt, and full recovery scenarios must still be validated in staging.
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
- Historical note: PR #5 was subsequently merged into main; see Current verified state at the top of this document. Passing CI is not production certification.


## Tenant pagination and approval-policy concurrency hardening — 2026-11-16

- Tenant collection APIs for invoices, suppliers, purchase orders, and approval policies now use bounded pagination (default 100, maximum 200) with deterministic ordering and tenant filters. Active-session listing is also bounded to 100.
- Frontend invoice queue now requests pages of 100 and provides Previous/Next navigation.
- Approval-policy create/deactivate operations serialize range mutations using a tenant-scoped PostgreSQL transaction advisory lock. Creation checks only for overlapping active amount ranges using half-open interval semantics, rather than loading all tenant policies.
- Added regression tests for pagination, tenant scoping, invalid pagination values, PostgreSQL advisory-lock behavior, and overlap-query predicates.
- CI run [43844747286](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43844747286) passed all three jobs before the latest approval-policy concurrency changes.
- CI run [43845374167](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43845374167) on frontend pagination commit `23da5bc0a58b0cdda7ab2a9f54b8b65a915376e5`: backend and frontend jobs passed; Docker image build was still running at last check.
- Subsequent approval-policy changes are on PR #5 and require a fresh successful full CI run. Historical note: PR #5 was subsequently merged into main; see Current verified state at the top of this document.
- Passing CI does not validate a live cloud deployment. Staging, cloud IAM/Object Lock/KMS validation, real restore drills, identity lifecycle completion, independent security/load testing, and operational sign-off remain release gates.


## Workspace and audit pagination update — 2026-11-16

- Audit event listing now accepts bounded `limit` (default 100, maximum 200) and non-negative `offset`, uses tenant scoping, and orders by creation time plus ID for stable pagination.
- Frontend workspace-member and audit-history tables now page through 100 records at a time; member API requests remain administrator-only and tenant-scoped.
- Added audit endpoint pagination coverage to the bounded collection regression tests and documented the audit pagination parameters and examples.
- Dashboard KPI labels now explicitly say "on page" / "loaded" because these figures describe the currently fetched page, not global tenant totals.
- Earlier CI run [43845791004](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43845791004) passed backend, frontend, and Docker on commit `f54d2fb7a427e532fbf19aa06114c54f9c73806f`.
- New pagination and dashboard-label changes require a fresh complete CI pass before being treated as verified.


## Supplier and purchase-order selector pagination — 2026-11-16

- Frontend supplier and purchase-order selectors now fetch 100 records per page from the bounded tenant APIs.
- Added Previous/Next controls to supplier and purchase-order creation panels so users can select records beyond the first 100 without unbounded API responses.
- Existing invoice, member, and audit pagination remains in place. KPI labels describe page-scoped counts rather than implying tenant-wide totals.
- Latest complete CI before this selector change: [43846321589](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43846321589) passed backend migrations, restricted runtime-role bootstrap, compileall, pytest, frontend dependency audit/build, Docker operational checks, Compose validation, and both image builds.
- The selector pagination commit requires its own complete CI run before verification.


## Tenant pagination query indexes — 2026-11-16

- Added migration `0018_tenant_pagination_indexes` for composite indexes supporting tenant-scoped ordering on users, suppliers, purchase orders, invoices, audit events, and approval policies.
- Invoice status-filtered pagination has a matching tenant/status/created-time index; audit history has both timestamp-ordered listing and ID-ordered export indexes.
- Added matching SQLAlchemy model metadata and regression assertions that verify the composite indexes are declared.
- Supplier and purchase-order dropdowns now page through 100 records with Previous/Next controls.
- CI run [43846321589](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43846321589) passed all three jobs before the supplier/PO selector and index changes. The latest migration and index changes require their own complete CI pass.


## Idempotency cleanup index — 2026-11-16

- Added migration `0019_idempotency_cleanup_index` and matching model metadata for the tenant/time/ID index used by bounded idempotency-retention cleanup.
- Added a regression test that asserts the composite index column order matches the cleanup query's tenant filter and oldest-first ordering.
- CI run [43847150797](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43847150797) passed all jobs on the previous PR head before this migration. The new migration and test require a fresh full CI pass.


## Auth-session query indexes — 2026-11-16

- Added migration `0020_auth_session_query_indexes` for tenant/user active-session listing and tenant/family revocation paths.
- Added model metadata and regression assertions for both composite index column orders.
- These indexes complement the existing tenant/expiry cleanup index; the migration must pass the latest backend and full CI workflow before being considered verified.


## Password lifecycle hardening (2026-11-16)

- Added authenticated self-service password change with current-password verification, minimum 12-character new-password validation, and password-reuse rejection.
- When MFA is enabled, password change requires a fresh TOTP step or consumes one unused recovery code; the user row is locked while credentials and MFA state are updated.
- Successful password changes revoke all active refresh sessions, clear refresh/CSRF cookies, and append an audit event. Existing short-lived access tokens remain valid until their configured expiry.
- Added regression coverage for invalid current password, password reuse, mandatory MFA, TOTP success, recovery-code consumption, refresh-session revocation, audit recording, and cookie clearing.
- This does not replace email verification, password-reset delivery, SSO/OIDC, or independent production security review.


- Frontend account-security settings now expose the password-change workflow with current/new password fields and conditional TOTP/recovery-code inputs when MFA is enabled. Successful changes return the user to sign-in with a clear confirmation message.


## Latest verification refresh — 2026-11-16

- GitHub Actions run [43850651897](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43850651897) passed all three jobs on commit `e9f381b4b2792fe806ad8a7506b63b42229210b6`: backend migrations through the current Alembic head, restricted runtime-role bootstrap, Python compilation and pytest; frontend dependency audit and production build; Docker operational-script regression checks, Compose validation, and backend/frontend image builds.
- This passing run includes migration `0020_auth_session_query_indexes`, the password-change backend endpoint and regression tests, and the frontend account-security password-change form.
- Historical note: PR #5 was subsequently merged into main; see Current verified state at the top of this document. This CI result verifies repository automation only; it does not establish production readiness or replace staging, cloud integration, recovery drills, independent security testing, and operational sign-off.


## Password-change abuse protection — 2026-11-16

- Password changes now share the persistent login throttle: 10 failed authentication attempts per email/IP pair within 15 minutes, serialized across PostgreSQL-backed API replicas with the existing advisory locks.
- Incorrect current-password checks and failed MFA challenges are persisted as failed attempts; successful password changes record a successful attempt in the same transaction as the password update and session revocation.
- The endpoint acquires email/IP attempt locks before the account row lock, matching login's lock order and avoiding a cross-endpoint lock inversion.
- Added regression coverage for persisted failed attempts, successful attempt recording, and HTTP 429 throttling with Retry-After.
- This change is on PR #5 and requires the current full CI run before it is considered verified.


## MFA brute-force protection — 2026-11-16

- MFA setup (current-password confirmation), TOTP enrollment confirmation, and MFA disablement now share the distributed login/password-change failure budget: 10 failures per email/IP within 15 minutes.
- Failed password, TOTP, and recovery-code checks are persisted in the existing login-attempt table using keyed HMAC email/IP identifiers; successful sensitive authentication checks are also recorded.
- PostgreSQL advisory locks are acquired before account row locks, preserving a consistent lock order across login, password change, and MFA state changes.
- MFA setup now locks the active account row before checking/encrypting the pending secret, preventing concurrent setup requests from silently overwriting enrollment state.
- Added regression tests for the shared throttle response and privacy-preserving attempt records. CI run is pending for these changes.

- API examples now document MFA enrollment/enable/disable requests and the shared throttling behavior. Implementation commits: `d0ef0a58`, `3a1b3274`; tests: `bc1a3ea5`; security and API docs: `4c7e53cd`, `62ffae40`.


## MFA disable regression-test correction — 2026-11-16

- Corrected the MFA-disable unit test to invoke the FastAPI endpoint with its actual `request` and `response` parameters.
- Isolated the persistent sensitive-authentication throttle helpers in this unit test; dedicated tests continue to cover the shared throttle behavior.
- Commit: `76aae99b8adb2978915ff1d2f042ec73e7a24db4`.
- A new full CI result for this exact commit is required; no green result is claimed yet.

## MFA regression correction — verified by full CI (2026-11-16)

- Corrected the MFA-disable regression test to call the endpoint with its actual `request` and `response` parameters.
- Isolated the shared sensitive-authentication throttle helpers in this unit test; dedicated throttle tests remain responsible for exercising the persistent throttle behavior.
- GitHub Actions run [43856458704](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43856458704) completed successfully on PR head `87638a9c81ead24c799dd7ca53ffd1793e9d9c8e`.
- Verified job results: backend (Alembic migrations, restricted runtime-role bootstrap, Python compilation, and pytest), frontend (dependency audit and production build), and Docker (operational-script tests, backup/restore checks, container hardening, Compose validation, and image builds) all passed.
- This is repository CI evidence only. It does not replace staging/UAT, provisioned cloud validation, independent security testing, real backup/restore drills, or production sign-off.
- PR #5 remains open and unmerged pending review and production acceptance.

## Workflow-run retention cleanup — 2026-11-16

- Updated `.github/workflows/cleanup-failed-runs.yml` to remove completed runs whose conclusion is `cancelled` or `failure`; successful runs are preserved.
- The workflow checks all pages of completed runs and supports manual dispatch.
- Scheduled cadence is every five minutes. GitHub Actions does not support a one-minute scheduled-workflow interval.
- Workflow change commit: `61bc76247153cb8dc88eebf1ce98e492c83bbe69`.
- The cleanup workflow is on PR #5's branch and will not become active on `main` until the PR is merged. The latest fully successful CI run before this workflow change is [43856458704](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43856458704); the current head requires a fresh CI result.

- Replaced the obsolete workflow path that was removed from `main` with the new `.github/workflows/cleanup-failed-runs.yml` path so the cleanup automation can be reviewed and merged without restoring the deleted file.

## Workflow cleanup implementation detail — 2026-11-16

- The scheduled workflow now delegates deletion to `scripts/cleanup_workflow_runs.sh`, using the runner's `GH_TOKEN` and GitHub CLI pagination across completed workflow runs.
- The script selects only `failure` and `cancelled` conclusions; successful and in-progress runs are excluded.
- Added the cleanup script to the Docker job's `bash -n` validation in `.github/workflows/ci.yml`.
- GitHub Actions schedule remains every five minutes (one-minute schedules are unsupported).
- The latest full CI success recorded above predates this workflow/script change; the current branch head must pass CI before this automation is considered verified.

## Workflow cleanup regression coverage — 2026-11-16

- Added `scripts/test_cleanup_workflow_runs.py` with a fake GitHub CLI to verify that only completed `failure` and `cancelled` runs are selected for deletion; successful and timed-out runs are preserved.
- Added checks that the workflow script refuses to run without its repository/token environment.
- Added this regression test to the Docker CI job and included the cleanup script in shell syntax validation.
- A local smoke test of the cleanup script with a fake CLI passed for the deletion filter and required-environment guards.
- The latest full GitHub Actions success remains run [43856458704](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43856458704), which predates these latest workflow changes. CI for the current head is still required.

## API route authentication guard — 2026-11-16

- Added an AST-based backend regression test that checks every `/api/v1/*` business route for an explicit `get_current_user` or `require_roles` dependency.
- The test permits only the intentionally public account-entry endpoints: registration, login/token issuance, refresh, and logout (which validates the presented refresh session).
- New business routes that omit an authentication/role dependency will fail the backend pytest suite.
- This is a static dependency-presence guard; it complements, but does not replace, endpoint-level authorization, tenant-isolation, and penetration tests.
- Added as `backend/tests/test_route_authentication.py`; current PR CI must execute it before the guard is considered verified.


## Latest full CI verification — 2026-11-16

- GitHub Actions run [43858350393](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43858350393) completed successfully on the current PR #5 head `38e07400e81e9d31aa7205bb67961cbb1939a130`.
- Backend job passed: Alembic migrations, restricted runtime-role bootstrap, Python compilation, and the complete pytest suite (including the AST API-route authentication guard).
- Frontend job passed: dependency advisory reporting, high-severity audit enforcement, and Next.js production build.
- Docker job passed: operational-script validation, workflow-run cleanup safety regression test, production startup/migration guards, PostgreSQL backup/restore checks, non-root container and health-check checks, Compose validation, and backend/frontend image builds.
- This verifies the repository CI pipeline at this commit only. It is not evidence of cloud provisioning, staging UAT, production deployment, independent security review, or a real disaster-recovery exercise.
- Historical note: PR #5 was subsequently merged into main; see Current verified state at the top of this document. The workflow-run cleanup schedule is every five minutes (GitHub Actions scheduled workflows do not support one-minute intervals) and will only become active on `main` after the PR is merged.


## Account-wide access-token revocation (2026-11-16)

- Added migration 0021_access_token_version and a zero-defaulted users.auth_token_version field.
- New access tokens carry the account version; authenticated requests compare it to the current user row and reject stale tokens.
- Password changes, MFA disablement, logout-all, member role changes, and member deactivation increment the version in their existing account transaction while revoking refresh sessions.
- Existing access tokens without a version claim remain compatible at version zero until they expire.
- Added regression tests for version claims, stale-token rejection, and legacy-token compatibility.
- The change is committed to PR #5 and is awaiting its own complete CI run. It has not been merged or deployed.

- Added route-level AST coverage to prevent sensitive account lifecycle handlers from omitting access-token invalidation; expanded endpoint tests for MFA disablement and logout-all.
- CI run 43859647111 passed backend, frontend, and Docker for the implementation commit before these final regression-test additions; the newest commit has its own CI run pending.


## Latest full CI verification — 2026-11-16 (continued)

- GitHub Actions run [43860322756](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43860322756) completed successfully on PR #5 head `7d682fa6ac286dcb350b17c501152f083f5e0131`.
- Backend passed: dependency installation, Alembic migrations, restricted PostgreSQL runtime-role bootstrap, Python compilation, and the complete pytest suite.
- Frontend passed: dependency advisory reporting, high-severity dependency audit enforcement, and Next.js production build.
- Docker passed: operational-script validation, workflow cleanup safety tests, production startup/migration guards, PostgreSQL backup/restore checks, non-root/service-health checks, Compose validation, and backend/frontend image builds.
- Added `docs/CI_VERIFICATION_2026-11-16.md` to preserve the exact revision, run URL, checks, and production release boundary.
- This result verifies automated CI only; it does not establish staging or production deployment, cloud security configuration, external email delivery, independent security review, or operational acceptance.
- Historical note: PR #5 was subsequently merged into main; see Current verified state at the top of this document. Workflow cleanup remains branch-only until a separately authorized merge.
