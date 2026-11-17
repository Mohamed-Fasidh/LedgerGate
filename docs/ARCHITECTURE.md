# Architecture

## Runtime flow
Next.js dashboard → FastAPI → authenticated user/tenant context → SQLAlchemy → PostgreSQL.

## Invoice lifecycle
`received` → `extraction_review` → human field confirmation → `received` → deterministic reconciliation → `review_required` → reviewer `approved` or `rejected`.

Every reconciliation is advisory. Even a clean match enters human review. No endpoint sends money.

## Agent runtime
`START → LOAD_RECORDS → VALIDATE → DECIDE → COMPLETE | HUMAN_REVIEW | FAILED`

The state machine performs deterministic checks. The allowlisted `ToolExecutor` validates tool names and strict Pydantic argument schemas before tenant-scoped data access. Tool schemas reject unexpected fields, including attempts to supply a tenant override.

The optional `TaskPlanner` uses the shared Ollama/vLLM inference client to propose a bounded JSON plan. It validates every proposed tool name and argument against the executor's registry and enforces request, response, and step limits. It does not execute tools: the caller must pass each validated step to the existing executor, which retains the authenticated tenant ID. The planner is not yet a persistent multi-turn orchestration engine and must not be treated as an approval authority.

The MCP process delegates to the same authenticated HTTP API.

## Retrieval and memory
Policy retrieval combines token overlap and character n-gram similarity. This is a reproducible lexical/fuzzy baseline; it is not a neural embedding model. Episodic/semantic/procedural memory records are tenant-scoped and ranked by deterministic token overlap. The service interfaces can be extended with a sentence-transformer embedding provider and pgvector index after benchmarking.

## Inference
Ollama and vLLM adapters can explain already-computed findings and support bounded task planning. They are not used to compute the financial decision. Provider calls have bounded timeouts and return an error if not configured. Benchmark output distinguishes measured values from unavailable telemetry.

## Tenant boundaries
All business records include tenant_id and API queries filter by the authenticated user's tenant. Before production, enforce PostgreSQL RLS and use a non-owner role; application filters alone are not a complete defense against every query bug.

## Data flow and trust boundaries
- Uploaded files are untrusted input.
- Extracted fields are candidates, never trusted facts until user confirmation.
- Retrieved policy text is evidence, not executable instructions.
- Model output and proposed tool plans are untrusted until schema, allowlist, and application authorization checks pass.
- Only authorized human reviewers can finalize invoice decisions.
