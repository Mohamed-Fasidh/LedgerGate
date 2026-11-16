# CI verification snapshot — 2026-11-16

## Verified revision

- Repository: `Mohamed-Fasidh/LedgerGate`
- Pull request: [#5 — Production readiness V2](https://github.com/Mohamed-Fasidh/LedgerGate/pull/5)
- Branch: `hardening/production-readiness-v2`
- Verified head: `659bcac187f570fc2e5cf5a9592ece15f6b5c584`
- GitHub Actions run: [43872087422](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43872087422)
- Result: **success** — backend, frontend, and Docker jobs all completed successfully.

## Checks observed

### Backend
- Dependency installation
- Alembic migrations, including the idempotent password-reset-token migration
- Restricted PostgreSQL runtime-role bootstrap
- Python compileall
- Full pytest suite (**223 passed**), including duplicate-registration anti-enumeration, email-verification lifecycle, consistent user-then-token lock ordering, password-reset token, SMTP STARTTLS, production configuration, and public-route origin-guard tests

### Frontend
- Dependency advisory report
- High-severity dependency audit gate
- Next.js production build, including the password-reset request and confirmation UI

### Docker and operations
- Operational script validation and workflow-run cleanup safety tests
- Production API startup and one-shot migration guard
- PostgreSQL backup publication and restore-drill verification tests
- Non-root container and service-health checks
- Docker Compose validation
- Backend and frontend image builds

## Email verification and password recovery verified by code and CI

- New registrations create an unverified account and send a 48-byte random verification token; only its SHA-256 hash is persisted. Verification tokens expire after 24 hours, are single-use, and are carried in a URL fragment so they are not sent in the HTTP request target or ordinary server access logs.
- Sign-in is blocked until email verification succeeds. A generic resend endpoint shares the persistent per-email/IP throttle and invalidates prior outstanding verification links. Existing accounts are treated as verified by the migration default to avoid locking out current users.
- Password reset tokens expire after 30 minutes, are single-use, and a new request invalidates prior outstanding links. Password-reset requests are limited to active, verified accounts.
- Registration, verification-resend, and password-reset request responses do not disclose whether an account exists; requests share the existing per-email/IP throttle. Existing unverified accounts can be sent a fresh link without changing the response.
- Confirmation requires a strong password and, when MFA is enabled, a fresh TOTP or unused recovery code. Confirmations and concurrent resends lock the account row before token rows, avoiding lock-order deadlocks.
- Completion increments the account access-token version, revokes refresh sessions, clears browser auth cookies, and records an audit event.
- SMTP delivery uses authenticated STARTTLS on port 587. Production startup requires SMTP host/credentials/from-address and an HTTPS public-app URL.
- CI validates the implementation and SMTP client behavior; it does not prove delivery through a real provider or mailbox receipt. A durable outbox/retry worker and external delivery monitoring remain production follow-ups.

## Workflow-run cleanup

- The canonical workflow is `.github/workflows/cleanup-unsuccessful-runs.yml`.
- It is scheduled every five minutes and can also be run manually.
- It targets completed failed and cancelled workflow runs only; it does not delete successful or in-progress runs.
- GitHub Actions scheduled workflows do not support a one-minute interval.
- The workflow is present on the PR branch. Its activation on `main` is governed by the merge of PR #5; this verification does not claim the PR branch changes have been merged.

## Interpretation and release boundary

This is automated CI evidence for the exact PR head listed above. It does not establish that a live staging or production environment has been deployed or validated. It does not prove cloud IAM, TLS/WAF, S3 Object Lock, key custody/rotation, backup recovery, monitoring, or external email delivery.

SSO/OIDC, real SMTP provider delivery, durable email retries/outbox, cloud provisioning, staging UAT, and real recovery drills remain outstanding. PR #5 remains open and unmerged. No merge or deployment is performed by this verification record; merge still requires explicit authorization and appropriate independent review.
