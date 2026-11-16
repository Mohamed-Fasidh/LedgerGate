# Build and verification status

## Implemented in repository
- FastAPI API, PostgreSQL, Alembic migrations, Next.js dashboard, and Docker Compose.
- JWT authentication, role checks, tenant-scoped queries, PostgreSQL FORCE RLS, and pooled-connection tenant-context cleanup.
- Separate migration-owner and non-owner `ledgergate_runtime` database connections; runtime role is provisioned without superuser or BYPASSRLS privileges.
- Append-only PostgreSQL audit mutation trigger and audit events for invoice file downloads.
- Invoice creator attribution, maker-checker approval/rejection, optimistic concurrency versioning, and tenant-scoped invoice-create idempotency.
- Persistent login throttling using hashed email/IP identifiers (10 failures per 15-minute window).
- S3-compatible private invoice storage adapter, server-side encryption, tenant-prefixed object keys, tenant-authorized downloads, and no-store download responses.
- ClamAV INSTREAM scanning before document parsing and storage; scanner failures fail closed.
- PDF page-count and extracted-text size limits; human confirmation of extracted invoice fields.
- Deterministic invoice/PO reconciliation, approval/rejection, agent tool allowlists, MCP adapter, lexical/fuzzy retrieval, and advisory-only model integrations.
- GitHub Actions for PostgreSQL migrations, runtime-role bootstrap, backend tests, frontend build, Compose validation, and Docker image builds.

## Latest verification
- Latest GitHub Actions run 421: backend migrations passed; runtime-role bootstrap passed; Python compilation passed; pytest passed; frontend production build passed.
- Compose configuration validation passed.
- Backend/frontend Docker image build was still running at the last status check; do not mark it passed until the workflow reports success.
- Earlier completed workflow run passed Docker image builds, but it predates the latest code changes.
- PostgreSQL security tests run against a disposable PostgreSQL service with a restricted non-owner role.

## Remaining production gates
- SSO/OIDC, MFA, verified email, password reset, refresh-token rotation/revocation, and session/device management.
- External WORM audit export, cryptographic event chaining/signing, and independent audit retention.
- Idempotency for remaining state-changing endpoints (supplier/PO creation, extraction confirmation, reconciliation, approval/rejection, policy and memory writes).
- Configurable multi-stage approval policies and thresholds.
- Customer-managed KMS encryption, bucket lifecycle/retention/access logging, and cloud IAM review.
- Production bucket, ClamAV signature updates, secrets manager, TLS/WAF, monitoring, backup/PITR and restore drills.
- Benchmark-backed embedding/vector retrieval if semantic RAG is required; current retrieval remains lexical/fuzzy.
- MCP runtime and model-provider integration verification.
- Load testing, independent penetration testing, privacy/legal review, and incident-response procedures.

LedgerGate remains an invoice verification and human-approval workflow. It does not initiate payments, and passing CI does not certify the platform for regulated financial production use.