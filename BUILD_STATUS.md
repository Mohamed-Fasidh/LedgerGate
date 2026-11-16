# Build and verification status

## Implemented in repository
- FastAPI API, PostgreSQL, Alembic and Docker Compose foundation.
- JWT authentication, tenant-scoped records, role checks and tenant-context-aware PostgreSQL RLS migration.
- Append-only audit mutation trigger for PostgreSQL.
- Invoice creator attribution, maker-checker approval/rejection, optimistic concurrency versioning, and tenant-authorized invoice file download.
- CI workflow now provisions PostgreSQL and exercises RLS under a restricted non-owner role; frontend production build is part of CI.
- Invoice/PO intake, extraction confirmation, deterministic reconciliation and human approval.
- Allowlisted agent tools, MCP adapter, lexical/fuzzy retrieval and advisory model adapters.
- Next.js dashboard, docs and evaluation suite.

## Verification status
- Previously recorded: Python source compilation and deterministic evaluation smoke suite (5/5 designed cases).
- Frontend production build passed in GitHub Actions on the first workflow attempt.
- The first backend CI attempt exposed an Alembic logging configuration error; this was fixed by removing the invalid logging-file initialization. A subsequent backend workflow run is in progress; do not treat migrations or tests as passing until it completes.
- Docker image build, MCP runtime, model-provider connectivity, malware scanning and external WORM audit export remain unverified.
- PostgreSQL security test is opt-in via TEST_POSTGRES_URL and requires a disposable database with migrations and test fixtures.

## Remaining production gates
- Create separate migration and runtime DB roles; runtime must not own tables and must not have BYPASSRLS.
- Private encrypted object storage and malware scanning.
- External WORM audit retention and signing.
- SSO/MFA, token rotation/revocation, rate limiting and idempotency.
- Dual approval, concurrency controls, backup/restore drills, security review, penetration/load testing and privacy/legal review.
- Replace lexical retrieval baseline with benchmarked embeddings/vector retrieval if semantic RAG is a product requirement.
- Execute full CI and production deployment checks before claiming release readiness.
