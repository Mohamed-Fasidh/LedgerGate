# LedgerGate
**Intelligent Enterprise Financial Verification & Agent Reliability Platform**

LedgerGate is a production-oriented reference implementation for invoice intake, purchase-order reconciliation, evidence-backed review, human approval, auditability, and AI-agent evaluation.

> Important: This repository is a deployable foundation, not a certified payment system. It does not initiate payments or autonomously approve them. Before handling real financial data, complete the deployment/security checklist, configure managed secrets and storage, perform a security review, and run load/penetration tests.

## Included
- FastAPI backend with JWT authentication, tenant-scoped data access, role checks, and PostgreSQL support.
- Invoice and purchase-order records, upload endpoint, deterministic reconciliation, duplicate detection, approval/rejection workflow, and audit events.
- Custom deterministic agent state machine with validated tool calls and explicit human-review escalation.
- Optional bounded LLM task planner and read-only orchestrator. Plans are schema-validated against an explicit read-only tool allowlist, then executed sequentially under the ToolExecutor's fixed authenticated tenant context with step limits, cooperative cancellation, and a deadline between calls. This in-process coordinator is not durable and cannot interrupt a synchronous database call already in progress; it cannot approve or mutate financial records.
- MCP-compatible tool server entry point for invoice/PO lookup and policy retrieval. See [MCP integration and security setup](docs/MCP_INTEGRATION.md) for local stdio configuration, token handling, HTTPS requirements, and tool side effects.
- Tenant-scoped deterministic lexical/fuzzy retrieval baseline and a versioned agent-evaluation harness. Neural embedding/vector retrieval is not yet implemented; see the documented research limitations.
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
- `POST /api/v1/agent/tasks` (authenticated, bounded, read-only agent task execution)
- `POST /api/v1/invoices/{id}/reconcile`
- `POST /api/v1/invoices/{id}/approve`
- `POST /api/v1/invoices/{id}/reject`
- `GET /api/v1/audit-events`
- `GET /health`

See `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `docs/EMAIL_OUTBOX.md`, and `docs/RESEARCH_PLAN.md`.

## Inference engineering

The backend includes a shared bounded client for Ollama and OpenAI-compatible vLLM, with streaming, timeouts, transient retries, queue backpressure, token/latency usage, optional Prometheus/GPU telemetry, and benchmark JSON/CSV output. Install optional benchmark tools with `pip install -r backend/requirements-inference.txt`; model-serving runtimes remain separate from the API image. See [Inference engineering and benchmarks](docs/INFERENCE_ENGINEERING.md). No GPU or live-provider performance results are claimed unless produced by an actual benchmark run.


## Research and evaluation

- [Transformer from scratch](research/transformer/README.md): educational GPT-style decoder implemented in PyTorch.
- [Reinforcement-learning experiments](research/reinforcement-learning/README.md): policy gradients, actor-critic, PPO/GAE, and educational GRPO.
- [Agent evaluation](research/agent-evaluation/README.md): versioned deterministic datasets, evaluator CLI, JSON/CSV reports, and optional live backend contracts.
- [Vizuara workshops](docs/workshops/README.md): four 15–20 minute teaching plans.

Run the deterministic agent-evaluation suite from its package directory with `python -m pip install -e ".[test]"`, then `python -m eval_framework.cli run --ci --output-dir outputs/local`. The included fixture scores are evaluator contract checks, not production LLM accuracy.

## Vizuara teaching workshops

Four 15–20 minute teaching plans with mathematical explanations, coding demonstrations, interview questions, and exercises are available in [docs/workshops](docs/workshops/README.md). They cover Transformers from first principles, AI agent engineering and evaluation, PPO/GRPO, and LLM inference/GPU performance. Workshop materials are instructional plans; benchmark outputs must be generated on the actual hardware and model server used.


### Read-only agent task API

Send an authenticated task description; the server derives tenant scope from the access token and does not accept a tenant ID from the client.

```bash
curl -X POST http://localhost:8000/api/v1/agent/tasks \
  -H "Authorization: Bearer <access-token>" \
  -H "Content-Type: application/json" \
  -d '{"task":"Find policy evidence relevant to duplicate invoices"}'
```

The endpoint uses the configured inference provider (`INFERENCE_PROVIDER=ollama` or `vllm`), validates the model-generated plan against read-only tool schemas, and limits execution to five steps and 30 seconds. It returns a structured plan, tool outputs, and a redacted execution trace. It cannot approve, reject, pay, or modify financial records. Configure and test the model server separately; the API does not install or launch Ollama/vLLM for you. This is synchronous in-process execution, not a durable job queue.
