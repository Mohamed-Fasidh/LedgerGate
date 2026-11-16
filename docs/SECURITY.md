# LedgerGate security posture and production-readiness gates

Last reviewed: 2026-11-16

## Implemented in the application

- Password hashing, short-lived access JWTs, persisted refresh sessions, single-use refresh-token rotation, reuse detection and token-family revocation, logout, and sign-out-all.
- Password changes share the login endpoint's persistent 10-failure/15-minute email-and-IP throttle. Incorrect current passwords and failed MFA challenges are recorded using keyed HMAC identifiers; PostgreSQL advisory locks serialize checks across replicas, acquired before the account row lock to preserve a consistent lock order.
- MFA enrollment, TOTP confirmation, and MFA disablement also share the persistent 10-failure/15-minute email-and-IP authentication budget. Failed password/TOTP/recovery checks are recorded with keyed HMAC identifiers, distributed PostgreSQL advisory locks are acquired before account row locks, and MFA enrollment serializes state changes to prevent concurrent setup races.
- Host-only HttpOnly refresh cookie, production Secure flag, SameSite=Lax, CSRF cookie/header validation, allowlisted Origin checks, and no refresh tokens in browser storage.
- Optional authenticator-app TOTP MFA with encrypted secrets, replay prevention, one-time hashed recovery codes, and session revocation when MFA is disabled.
- Persistent login and registration throttling using keyed HMAC identifiers, bounded retention cleanup, and PostgreSQL advisory locks to serialize limits across API replicas.
- Static AST regression coverage requires every `/api/v1/*` business route to declare an authenticated-user or role dependency; public registration, token issuance, refresh, and refresh-session logout routes are explicit exceptions and must remain separately reviewed.
- Scheduled workflow-run retention automation deletes only completed failed/cancelled runs, preserves successful and in-progress runs, and is configured for a five-minute cadence; it is branch-only until PR #5 is merged.
- Tenant-scoped authorization and PostgreSQL FORCE RLS, with tenant context set per request and cleared before pooled connections are reused. The runtime database role is separate from the migration/table-owner role and must remain non-owner and NOBYPASSRLS.
- Invoice intake, bounded document extraction, human confirmation of extracted fields, deterministic invoice/PO reconciliation, maker-checker controls, sequential amount-based approval policies, distinct approvers, policy snapshots, and optimistic concurrency protection.
- Tenant-wide idempotency for supported invoice and approval-policy write operations, request fingerprint conflict protection, replayed response handling, and regression coverage. Continue auditing every mutating endpoint as features evolve.
- Private S3-compatible invoice storage adapter, server-side encryption request, tenant-authorized downloads, upload size/type checks, and ClamAV scanning that fails closed if the scanner is unavailable. Production requires configured private object storage and a reachable scanner.
- Append-only audit mutation protection, per-tenant SHA-256 event chains, bounded chain verification, signed audit exports, and an offline export verifier. The export implementation targets S3 Object Lock COMPLIANCE retention.
- PostgreSQL backup and disposable restore-drill scripts, checksum validation, non-root containers, dropped Linux capabilities, no-new-privileges, dependency-aware API readiness, and CI regression checks.
- AI/agent outputs are advisory only; tool allowlists prevent the agent from approving invoices or initiating payments.

## Not yet production-verified

The repository contains implementation and automated checks, but the following require external provisioning or independent operational evidence:

1. **Identity lifecycle:** SSO/OIDC, verified email, password reset/account recovery, and complete administrator-assisted recovery procedures.
2. **Cloud infrastructure:** provision staging/production, managed PostgreSQL, private networking, TLS/reverse proxy, WAF/rate limiting, managed secrets, workload IAM, monitoring, and alerting.
3. **Object storage and keys:** provision a private versioned bucket and S3 Object Lock COMPLIANCE retention; validate bucket policy and IAM in the target account; establish independent signing/encryption key custody and rotation. Current storage requests SSE-S3, not customer-managed SSE-KMS.
4. **Database operations:** run migrations as a one-shot release job for multi-replica deployments; validate grants and cross-tenant negative tests using the exact production runtime role.
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
