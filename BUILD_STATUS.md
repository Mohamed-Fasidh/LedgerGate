# Build and verification status

## Implemented in repository
- FastAPI API, PostgreSQL, Alembic and Docker Compose foundation.
- JWT authentication, tenant-scoped records, role checks and tenant-context-aware PostgreSQL RLS migration.
- Append-only audit mutation trigger for PostgreSQL.
- Invoice creator attribution, maker-checker approval/rejection, optimistic concurrency versioning, tenant-authorized invoice file download, and tenant-scoped invoice-create idempotency keys.
- CI provisions PostgreSQL, exercises RLS under a restricted non-owner role, validates Compose configuration, and builds backend/frontend containers.
- Invoice/PO intake, extraction confirmation, deterministic reconciliation and human approval.
- Allowlisted agent tools, MCP adapter, lexical/fuzzy retrieval and advisory model adapters.
- Next.js dashboard, docs and evaluation suite.

## Verification status
- Previously recorded: Python source compilation and deterministic evaluation smoke suite (5/5 designed cases).
- Frontend production build passed in GitHub Actions on the first workflow attempt.
- A GitHub Actions run passed the earlier RLS, migrations, backend tests, frontend build, Compose validation, and container build. A newer run is required to verify the latest runtime-role bootstrap script and idempotency migration.
- The latest S3/ClamAV implementation and restricted-role bootstrap are not yet verified by the completed CI run. Live cloud bucket/IAM configuration, malware signature freshness, MCP runtime, model-provider connectivity, and external WORM audit export remain unverified.
- PostgreSQL security test is opt-in via TEST_POSTGRES_URL and requires a disposable database with migrations and test fixtures.

## Remaining production gates
- Create separate migration and runtime DB roles; runtime must not own tables and must not have BYPASSRLS.
- S3-compatible private object storage adapter with server-side encryption and ClamAV INSTREAM scanning (fail closed); production startup requires these services.
- Separate PostgreSQL migration-owner and non-owner runtime-role bootstrap; API is configured to connect using `ledgergate_runtime`.
- External WORM audit retention and signing.
- SSO/MFA, token rotation/revocation, rate limiting and idempotency.
- Dual approval, concurrency controls, backup/restore drills, security review, penetration/load testing and privacy/legal review.
- Replace lexical retrieval baseline with benchmarked embeddings/vector retrieval if semantic RAG is a product requirement.
- Execute full CI and production deployment checks before claiming release readiness.
