# CI verification snapshot — 2026-11-16

## Verified revision

- Repository: `Mohamed-Fasidh/LedgerGate`
- Pull request: [#5 — Production readiness V2](https://github.com/Mohamed-Fasidh/LedgerGate/pull/5)
- Branch: `hardening/production-readiness-v2`
- Verified head: `00a1ef2baf1b37f671f155348fc08950986e8275`
- GitHub Actions run: [43868511911](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43868511911)
- Result: **success** — backend, frontend, and Docker jobs all completed successfully.

## Checks observed

### Backend
- Dependency installation
- Alembic migrations, including the idempotent password-reset-token migration
- Restricted PostgreSQL runtime-role bootstrap
- Python compileall
- Full pytest suite, including password-reset token, SMTP STARTTLS, production configuration, and public-route origin-guard tests

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

## Password recovery implementation verified by code and CI

- Reset tokens are generated from 48 bytes of cryptographic randomness; only their SHA-256 hashes are persisted.
- Tokens expire after 30 minutes, are single-use, and a new request invalidates prior outstanding links.
- Request responses do not disclose whether an account exists; requests share the existing per-email/IP throttle and email is dispatched after the generic response.
- Confirmation requires a strong password and, when MFA is enabled, a fresh TOTP or unused recovery code.
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

Verified email, SSO/OIDC, real SMTP provider delivery, durable email retries, cloud provisioning, staging UAT, and real recovery drills remain outstanding. PR #5 remains open and unmerged. No merge or deployment is performed by this verification record; merge still requires explicit authorization and appropriate independent review.
