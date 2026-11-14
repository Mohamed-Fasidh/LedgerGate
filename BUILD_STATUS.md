# Build and verification status

## Implemented in this branch
- Existing FastAPI application and PostgreSQL/Docker configuration.
- JWT authentication, workspace registration, admin-created tenant users, and role checks.
- Tenant-scoped suppliers, purchase orders, invoices, policies, memories, and audit events at the application-query layer.
- PDF/TXT upload, text extraction, regex-based candidate extraction, optional Tesseract OCR fallback.
- Human confirmation gate before invoice reconciliation.
- Deterministic invoice-vs-PO reconciliation for amount, currency, supplier, missing PO, and duplicate cases.
- Human review/approval/rejection; no payment initiation.
- Custom finite-state agent runtime and Pydantic allowlisted tool executor.
- MCP stdio tools delegating through a tenant-authenticated API token.
- Hybrid lexical/fuzzy retrieval baseline and tenant-scoped memory retrieval.
- Optional Ollama and vLLM advisory explanation adapters.
- Next.js dashboard, docs, Docker Compose, and Alembic migrations.
- Production configuration now rejects SQLite, non-PostgreSQL URLs, weak JWT secrets, wildcard/non-HTTPS CORS origins, unsupported JWT algorithms, and invalid token/upload limits.
- Password validation enforces bcrypt's 72-byte input limit; added focused password tests.
- Docker API container runs as non-root; database migrations run as a one-shot Compose service before API startup instead of in every API replica.
- Frontend public API and API-docs URLs are configurable at Next.js build time.
- GitHub Actions workflow added for Python compile/pytest and Next.js production build.
- PostgreSQL RLS migration added for suppliers, purchase orders, invoices, policy documents, agent memories, and audit events, using ENABLE + FORCE ROW LEVEL SECURITY and tenant-scoped USING/WITH CHECK policies.
- Authenticated requests now set a transaction-local tenant context; SQLAlchemy reapplies it on each transaction, including transactions opened after commits. Workspace registration sets its new tenant context before inserting the first user.
- Deployment documentation now requires separate migration-owner and non-owner, NOBYPASSRLS runtime database roles; the users table is explicitly documented as outside this RLS migration.
- Docker Compose now provisions a separate non-owner `ledgergate_runtime` role for API traffic; migrations continue to use the database bootstrap/migration account.
- Added a PostgreSQL RLS integration test covering no-context denial, tenant-only visibility, cross-tenant insert rejection, and tenant-context reapplication after commit.
- Audit events now carry per-tenant SHA-256 hash links; PostgreSQL writers serialize through transaction-scoped advisory locks and UPDATE/DELETE are blocked by a database trigger. Existing audit rows are backfilled by migration.
- Invoice approve/reject now support optional database-backed idempotency keys, request-hash conflict detection, same-response replay, tenant/user scoping, PostgreSQL advisory-lock serialization, and 24-hour expiry. The migration applies tenant RLS to the idempotency table.
- Tenant approval policies are append-only and versioned. Invoice records snapshot the policy version, threshold, and required reviewer count; later versions do not alter existing invoices. INR only at this stage.\n- High-value INR invoices use a configurable two-reviewer approval threshold (`DUAL_APPROVAL_THRESHOLD_INR`, default INR 100,000); each approval is persisted in a tenant-RLS-protected table, and approval status remains pending until two distinct reviewers have approved.
- Invoice list/detail API responses expose the required and recorded approval counts; the dashboard displays progress, and a tenant-scoped approval-history endpoint exposes reviewer IDs and timestamps.
- GitHub Actions backend job now starts PostgreSQL 16, applies Alembic migrations, and runs tests as the restricted runtime role.

## Validation status
- The GitHub branch and CI workflow have been created.
- The GitHub Actions workflow is configured to run on pushes to `main` and `production-readiness`, pull requests to `main`, and manual dispatch.
- Do not treat the branch as validated until the workflow completes successfully. The current editing environment cannot run the full Docker, PostgreSQL, npm, or integration-test stack locally.
- Earlier baseline checks (before this branch) included Python source compilation, 5/5 deterministic reconciliation smoke cases, and a parser smoke test. These are not substitutes for the new CI run or production integration testing.

## Known blockers before real production data
1. The PostgreSQL RLS migration and transaction-local tenant context now have a dedicated PostgreSQL 16 CI integration test. Wait for the latest CI run to pass before treating this change as validated. The users identity table is not covered by this RLS migration.
2. The initial Alembic migration uses SQLAlchemy `metadata.create_all`; replace it with a reviewed explicit baseline before production schema management.
3. Invoice files are stored on local disk. Private encrypted object storage, malware scanning, retention/deletion, and access-controlled downloads are not implemented.
4. SSO/OIDC, MFA, email verification, password reset, refresh-token rotation/revocation, and login throttling are not implemented.
5. Audit events are hash-chained and protected from ordinary UPDATE/DELETE by a PostgreSQL trigger, but external WORM replication and independent checkpoints remain outstanding.
6. Idempotency is implemented for invoice approve/reject only. Foreign-currency approval thresholds/FX conversion, idempotency for mutations beyond approve/reject, and broader optimistic locking remain outstanding. Maker-checker creator/decision-maker tracking is present.
7. No completed penetration test, load test, disaster-recovery restore drill, or jurisdiction-specific privacy/compliance review.
8. Render/Vercel deployment has not been executed or smoke-tested.

LedgerGate remains a deployable foundation/reference implementation, not a certified financial system. Do not use it to initiate payments or as the sole control for approving financial transactions.
