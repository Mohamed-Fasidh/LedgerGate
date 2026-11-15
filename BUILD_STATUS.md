# Build and verification status

## Implemented
- FastAPI backend, PostgreSQL, SQLAlchemy/Alembic migrations, Docker Compose, and Next.js dashboard.
- Workspace registration, JWT access tokens, bcrypt password hashing, role checks, TOTP MFA, login throttling, server-side session revocation, refresh-token rotation, and one-time password-reset tokens.
- Tenant-scoped supplier, purchase-order, invoice, policy-document, agent-memory, audit-event, idempotency, invoice-approval, approval-policy, and auth-session data paths.
- PostgreSQL FORCE ROW LEVEL SECURITY for tenant-owned business tables, approval policies, invoice approvals, idempotency records, and auth sessions, with transaction-local tenant context and a restricted non-owner runtime role in CI.
- PDF/TXT invoice intake, upload validation, fail-closed ClamAV scanning, text extraction/OCR fallback, and a human confirmation gate before reconciliation.
- Deterministic invoice-to-PO reconciliation for amount, currency, supplier, missing PO, and duplicate cases.
- Maker-checker controls, append-only versioned approval policies scoped by tenant and currency, invoice policy snapshots, and configurable distinct-reviewer approvals for high-value invoices.
- Approval-history and audit-event APIs, per-tenant SHA-256 audit hash chains, database protections against ordinary audit UPDATE/DELETE, and audit-chain verification.
- Database-backed idempotency for tenant-user, supplier, purchase-order, invoice creation/upload, extraction confirmation, reconciliation, approval/rejection, agent-memory, approval-policy, policy-document creation, and active-session revocation. Logout-all intentionally remains non-replayable because it revokes the caller's access token.
- Custom finite-state agent runtime, allowlisted tool executor, MCP stdio tools, tenant-scoped retrieval, and optional Ollama/vLLM advisory explanation adapters.
- Private S3-compatible storage implementation with tenant-scoped object keys, SSE-S3 upload requests, short-lived presigned downloads, and storage reconciliation tooling.
- Frontend invoice workflows, refresh-token retry, MFA enrollment/disable and TOTP login input, approval policy management, approval progress, audit event browsing and integrity status.
- GitHub Actions for backend checks with PostgreSQL RLS integration, frontend production build, and Docker image builds; CodeQL workflow is also present.
- Explicit PostgreSQL Alembic baseline with a fail-closed guard against applying it to a non-empty unversioned schema; the previous metadata-driven create_all baseline has been removed.

## Verification
- Latest LedgerGate CI run [#43743596983](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43743596983) passed on commit `a279e50cb8bf951ccaeb83af095e417e811cf5a0`.
- Latest CodeQL run [#43743596810](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/43743596810) passed on the same commit.
- CI includes PostgreSQL 16 migrations and backend tests using a restricted runtime role, frontend production build, and backend/frontend Docker image builds.
- These are CI results only. They are not a penetration test, live cloud deployment, operational recovery drill, or financial-services certification.

## Remaining production gates
1. The users, login_throttles, and password_reset_tokens tables remain outside tenant RLS because pre-authentication flows need identity lookup. The runtime DB credential currently has broad application-table grants. Design and test a narrowly scoped authentication data-access boundary before production.
2. Complete a live S3 integration and policy review: IAM/workload identity, bucket public-access block, encryption, lifecycle/retention, versioning, deletion, monitoring, and restore validation.
3. Configure production SMTP and an HTTPS password-reset page; SSO/OIDC and email verification remain outstanding.
4. Configure external WORM audit replication and independent integrity checkpoints.
5. Review remaining mutation routes for idempotency requirements and add concurrency/optimistic-locking coverage across all workflows.
6. Run a full security review, dependency/SAST/DAST scans, penetration test, abuse/rate-limit review, and threat-model sign-off.
7. Run load/performance tests, backup/PITR verification, disaster-recovery restore drills, alerting, and incident-response exercises.
8. Execute a real deployment with managed secrets, TLS ingress/WAF, private networking, least-privilege credentials, and end-to-end smoke tests.
9. Complete jurisdiction-specific privacy, legal, and financial-control review before processing real financial records.

## Scope and safety
LedgerGate is an invoice verification and approval workflow. It does not initiate payments. Automated extraction and reconciliation are advisory and require human review. Passing CI does not make this a certified financial system or establish production readiness. Do not use it as the sole control for approving financial transactions.
