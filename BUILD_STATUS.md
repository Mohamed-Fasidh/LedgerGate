# Build and verification status

## Implemented in this ZIP
- FastAPI application and PostgreSQL/Docker configuration.
- JWT authentication, workspace registration, admin-created tenant users, and roles.
- Tenant-scoped suppliers, purchase orders, invoices, policies, memories, and audit events.
- PDF/TXT upload, text extraction, regex-based candidate extraction, optional Tesseract OCR fallback.
- Human confirmation gate before invoice reconciliation.
- Deterministic invoice-vs-PO reconciliation for amount, currency, supplier, missing PO, and duplicate cases.
- Human review/approval/rejection; no payment initiation.
- Custom finite-state agent runtime and Pydantic allowlisted tool executor.
- MCP stdio tools delegating through a tenant-authenticated API token.
- Hybrid lexical/fuzzy retrieval baseline and tenant-scoped memory retrieval.
- Optional Ollama and vLLM advisory explanation adapters.
- Evaluation suite, unit tests, Next.js dashboard, docs, Docker Compose, and Alembic migrations.

## Checks performed in this environment
- Python source compilation succeeded.
- Deterministic evaluation smoke suite: 5/5 designed scenarios passed.
- Invoice extraction parser smoke test successfully detected a sample invoice number, amount, currency, and date.

## Not independently verified here
The environment could not reach the Python package index, so dependencies could not be installed and the full FastAPI integration tests, Docker image build, frontend npm build, PostgreSQL migrations, MCP runtime, OCR binary, and model-provider connections were not executed. The ZIP includes pinned dependencies and test/run instructions for verification on your machine.

## Still required before real production use
SSO/MFA, PostgreSQL RLS, non-owner DB role, private object storage, malware scanning, immutable/WORM audit, idempotency and dual approval, rate limiting, secrets management, backups/restore drills, security review, penetration testing, load testing, and jurisdiction-specific financial/privacy controls.
