# LedgerGate
**Intelligent Enterprise Financial Verification & Agent Reliability Platform**

LedgerGate is a production-oriented reference implementation for invoice intake, purchase-order reconciliation, evidence-backed review, human approval, auditability, and AI-agent evaluation.

> Important: This repository is a deployable foundation, not a certified payment system. It does not initiate payments or autonomously approve them. Before handling real financial data, complete the deployment/security checklist, configure managed secrets and storage, perform a security review, and run load/penetration tests.

## Included
- FastAPI backend with JWT authentication, tenant-scoped data access, role checks, and PostgreSQL support.
- Invoice and purchase-order records, upload endpoint, deterministic reconciliation, duplicate detection, approval/rejection workflow, and audit events.
- Custom deterministic agent state machine with validated tool calls and explicit human-review escalation.
- MCP-compatible tool server entry point for invoice/PO lookup and policy retrieval.
- Hybrid retrieval foundation (PostgreSQL full-text + pgvector-ready schema) and evaluation harness.
- Next.js dashboard starter with invoice review views.
- Docker Compose for PostgreSQL, API, web application, ClamAV, and a separate durable account-email worker.
- Alembic migrations, tests, sample environment, and operational documentation.

## Quick start
1. Copy `.env.example` to `.env` and replace all secrets.
2. Run `docker compose up --build`.
3. API docs: http://localhost:8000/docs
4. Web: http://localhost:3000
5. Register a user using `POST /api/v1/auth/register`, then sign in.

For local development without Docker:
- Backend: `cd backend && python -m venv .venv && pip install -r requirements.txt && uvicorn app.main:app --reload`
- Frontend: `cd frontend && npm install && npm run dev`

## Default workflow
1. Register and log in.
2. Create a supplier and purchase order.
3. Upload an invoice (PDF/text file) or create an invoice record through the API.
4. Run reconciliation.
5. Review evidence and discrepancies.
6. Approve or reject only through an authorized human reviewer.

## Security notes
- Account verification and password-reset emails are transactionally queued in an encrypted PostgreSQL outbox and delivered by a separate worker with bounded retries and crash recovery.
- Set a dedicated `EMAIL_OUTBOX_ENCRYPTION_KEY` (Fernet key) and protect it separately from JWT and MFA keys. The worker provides at-least-once delivery; a crash after SMTP accepts a message but before the sent-state commit can cause a duplicate.
- Set unique high-entropy `JWT_SECRET_KEY` and `APP_ENCRYPTION_KEY` values.
- Use HTTPS behind a trusted reverse proxy.
- Use managed PostgreSQL, encrypted object storage, managed secrets, backups, and restricted network access in production.
- Configure CORS to exact trusted origins.
- Add malware scanning and file-type/content validation before enabling public uploads.
- Configure identity provider/SSO, rate limiting, WAF, alerting, retention policies, and disaster recovery.
- Review tenant isolation and authorization tests before production.
- Do not store card data, bank credentials, or payment secrets in LedgerGate.
- AI output is advisory. Human authorization is required for financial decisions.

## API overview
- `POST /api/v1/auth/register`, `POST /api/v1/auth/token`
- `GET/POST /api/v1/suppliers`
- `GET/POST /api/v1/purchase-orders`
- `GET/POST /api/v1/invoices`
- `POST /api/v1/invoices/{id}/reconcile`
- `POST /api/v1/invoices/{id}/approve`
- `POST /api/v1/invoices/{id}/reject`
- `GET /api/v1/audit-events`
- `GET /health`

See `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `docs/EMAIL_OUTBOX.md`, and `docs/RESEARCH_PLAN.md`.
