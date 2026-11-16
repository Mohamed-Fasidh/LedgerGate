# Build and verification status

## Implemented in repository
- FastAPI API, PostgreSQL, Alembic and Docker Compose foundation.
- JWT authentication, tenant-scoped records, role checks and tenant-context-aware PostgreSQL RLS migration.
- Append-only audit mutation trigger for PostgreSQL.
- Invoice/PO intake, extraction confirmation, deterministic reconciliation and human approval.
- Allowlisted agent tools, MCP adapter, lexical/fuzzy retrieval and advisory model adapters.
- Next.js dashboard, docs and evaluation suite.

## Verification status
- Previously recorded: Python source compilation and deterministic evaluation smoke suite (5/5 designed cases).
- Not independently verified in this change: PostgreSQL RLS migration, database trigger, integration tests, Docker image build, frontend production build, MCP runtime or model-provider connectivity.
- PostgreSQL security test is opt-in via TEST_POSTGRES_URL and requires a disposable database with migrations and test fixtures.

## Remaining production gates
- Create separate migration and runtime DB roles; runtime must not own tables and must not have BYPASSRLS.
- Private encrypted object storage and malware scanning.
- External WORM audit retention and signing.
- SSO/MFA, token rotation/revocation, rate limiting and idempotency.
- Dual approval, concurrency controls, backup/restore drills, security review, penetration/load testing and privacy/legal review.
- Replace lexical retrieval baseline with benchmarked embeddings/vector retrieval if semantic RAG is a product requirement.
- Execute full CI and production deployment checks before claiming release readiness.
