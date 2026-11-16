# LedgerGate security posture and production-readiness gates

Last reviewed: 2026-11-16

## Implemented in the application

- Password hashing, short-lived access JWTs, persisted refresh sessions, single-use refresh-token rotation, reuse detection and token-family revocation, logout, and sign-out-all.
- Email verification for new registrations: accounts remain unable to sign in until a 48-byte random, single-use verification token is confirmed; only SHA-256 hashes are stored, tokens expire after 24 hours, resend responses are generic, and prior outstanding links are invalidated. Existing accounts are preserved as verified by migration default. Duplicate registration requests return the same generic 202 response as new registrations; an existing unverified account receives a fresh link without exposing account status. Link replacement and confirmation serialize on the account row.
- Email-based password recovery with 48-byte cryptographic tokens stored only as SHA-256 hashes, 30-minute expiry, single-use consumption, invalidation of prior links, generic request responses, shared per-email/IP throttling, authenticated SMTP STARTTLS, optional MFA verification, audit events, immediate access-token invalidation, refresh-session revocation, and auth-cookie clearing. Both verification and reset links use URL fragments so tokens are not included in HTTP request URLs. Password-reset issuance and confirmation lock the account row before token rows, matching the verification-link path and preventing deadlocks during concurrent resend/consume operations. Account-security emails are transactionally inserted into the encrypted `outbound_emails` table and delivered by the standalone worker. The encrypted payload uses a dedicated Fernet key; recipients and message type remain queryable metadata. Worker claims use PostgreSQL `FOR UPDATE SKIP LOCKED`, five-minute leases, lease-ownership checks on delivery/retry/cancellation writes, bounded exponential retry (12 attempts maximum), and a dead-letter state. Delivery is at-least-once: if SMTP accepts a message and the worker crashes before recording success, a duplicate can be sent after lease expiry. Alert on dead-letter rows and sustained queue age. Actual SMTP provider delivery is not yet verified.
- Password changes share the login endpoint's persistent 10-failure/15-minute email-and-IP throttle. Incorrect current passwords and failed MFA challenges are recorded using keyed HMAC identifiers; PostgreSQL advisory locks serialize checks across replicas, acquired before the account row lock to preserve a consistent lock order.
- MFA enrollment, TOTP confirmation, and MFA disablement also share the persistent 10-failure/15-minute email-and-IP authentication budget. Failed password/TOTP/recovery checks are recorded with keyed HMAC identifiers, distributed PostgreSQL advisory locks are acquired before account row locks, and MFA enrollment serializes state changes to prevent concurrent setup races.
- Host-only HttpOnly refresh cookie, production Secure flag, SameSite=Lax, CSRF cookie/header validation, allowlisted Origin checks, and no refresh tokens in browser storage.
- Optional authenticator-app TOTP MFA with encrypted secrets, replay prevention, one-time hashed recovery codes, and session revocation when MFA is disabled.
- Persistent login and registration throttling using keyed HMAC identifiers, bounded retention cleanup, and PostgreSQL advisory locks to serialize limits across API replicas.
- Static AST regression coverage requires every `/api/v1/*` business route to declare an authenticated-user or role dependency; public registration, token issuance, refresh, refresh-session logout, password-reset, and email-verification request/confirmation routes are explicit exceptions and must remain separately reviewed.
- A second AST regression check requires registration/login, password-reset, and email-verification routes to call the allowlisted-Origin guard and refresh/logout routes to call the CSRF guard before processing credentials or tokens.
- Scheduled workflow-run retention automation deletes only completed failed/cancelled runs, preserves successful and in-progress runs, and is configured for GitHub's minimum five-minute scheduled cadence. The workflow is merged into the default branch.
- Tenant-scoped authorization and PostgreSQL FORCE RLS, with tenant context set per request and cleared before pooled connections are reused. The runtime database role is separate from the migration/table-owner role, remains non-owner and NOBYPASSRLS, receives an explicit allowlist of current application-table grants, has read-only access to `alembic_version`, and receives no default privileges on future tables or sequences. Pre-authentication identity tables still require a narrower access boundary; see the remaining gates.
- Invoice intake, bounded document extraction, human confirmation of extracted fields, deterministic invoice/PO reconciliation, maker-checker controls, sequential amount-based approval policies, distinct approvers, policy snapshots, and optimistic concurrency protection.
- Tenant-wide idempotency for supported invoice and approval-policy write operations, request fingerprint conflict protection, replayed response handling, and regression coverage. Continue auditing every mutating endpoint as features evolve.
- Private S3-compatible invoice storage adapter, server-side encryption request, tenant-authorized downloads, upload size/type checks, and ClamAV scanning that fails closed if the scanner is unavailable. Production requires configured private object storage and a reachable scanner.
- Append-only audit mutation protection, per-tenant SHA-256 event chains, bounded chain verification, signed audit exports, and an offline export verifier. The export implementation targets S3 Object Lock COMPLIANCE retention.
- PostgreSQL backup and disposable restore-drill scripts, checksum validation, non-root containers, dropped Linux capabilities, no-new-privileges, dependency-aware API readiness, and CI regression checks.
- AI/agent outputs are advisory only; tool allowlists prevent the agent from approving invoices or initiating payments.
- The standalone MCP adapter requires HTTPS for non-loopback API origins, rejects API URLs containing credentials/path/query/fragment, disables redirects for bearer-authenticated requests, and uses the maintained MCP SDK v1.30.0. Loopback HTTP is allowed only for local development.
- GitHub CodeQL runs security-extended analysis for Python and JavaScript/TypeScript on pull requests and pushes to main, with a weekly scheduled scan.
- Third-party GitHub Actions used by CI and CodeQL are pinned to verified full commit SHAs. A CI regression check rejects mutable tags or incomplete action references.

## Not yet production-verified

The repository contains implementation and automated checks (including bounded agent-memory search and email-outbox lease fencing), but the following require external provisioning or independent operational evidence:

1. **Identity lifecycle:** SSO/OIDC, durable email-delivery outbox/retries, and complete administrator-assisted recovery procedures. Email verification and password reset are implemented, but real SMTP provider delivery, staging mailbox verification, and full recovery drills remain untested.
2. **Cloud infrastructure:** provision staging/production, managed PostgreSQL, private networking, TLS/reverse proxy, WAF/rate limiting, managed secrets, workload IAM, monitoring, and alerting.
3. **Object storage and keys:** provision a private versioned bucket and S3 Object Lock COMPLIANCE retention; validate bucket policy and IAM in the target account; establish independent signing/encryption key custody and rotation. Current storage requests SSE-S3, not customer-managed SSE-KMS.
4. **Database operations:** run migrations as a one-shot release job for multi-replica deployments; validate the explicit runtime grant allowlist and cross-tenant negative tests using the exact production runtime role. The runtime role still has direct access to pre-authentication identity records; implement and test a dedicated authentication data-access boundary before production.
5. **Recovery:** execute real isolated backup/restore drills, verify point-in-time recovery, and test audit-export verification and key rotation against provisioned cloud resources.
6. **Security assurance:** independent threat model, SAST/DAST, penetration testing, load testing, privacy/legal review, incident-response plan, and operational sign-off.
7. **Staging acceptance:** end-to-end multi-user MFA/recovery scenarios, tenant isolation, upload scanning, S3 permissions, alerts, and full staging UAT.

## Operational rules

- Never commit production credentials, signing keys, database passwords, or customer data.
- Use separate migration-owner and restricted API runtime database roles. The API runtime role must not own tenant tables and must not have BYPASSRLS.
- Keep object storage private; enable public-access blocking, versioning, encryption, TLS-only access, access logging, and approved retention policies.
- Configure ClamAV signature updates and resource limits. Scanner errors must continue to fail uploads closed.
- Keep audit signing keys outside the database and application repository. Protect keyring files and define rotation/revocation procedures.
- Do not treat CI success as production certification. Do not route production traffic until staging, recovery, security, and operational gates have been signed off.

## Product boundary

LedgerGate is an invoice verification and human-approval workflow platform. It does not initiate payments, hold funds, or autonomously approve financial transactions.


## Immediate access-token invalidation

Access tokens include an account-scoped auth_token_version claim. The API checks this claim against the active user row on every authenticated request. Password changes, MFA disablement, logout-all, tenant-member role changes, and tenant-member deactivation increment the version in the same database transaction as their account/session changes. Previously issued access tokens are therefore rejected immediately after those operations commit, rather than remaining usable until their JWT expiry. Existing tokens without the version claim are treated as version zero during the compatibility window; the migration initializes existing accounts to zero.

The route-level AST regression suite also asserts that password changes, MFA disablement, logout-all, member role changes, and member deactivation invoke the account-version invalidation helper.
