# Architecture

## Runtime flow
Next.js dashboard → FastAPI → authenticated user/tenant context → SQLAlchemy → PostgreSQL.

## Invoice lifecycle
`received` → `extraction_review` → human field confirmation → `received` → deterministic reconciliation → `review_required` → reviewer `approved` or `rejected`.

Every reconciliation is advisory. Even a clean match enters human review. No endpoint sends money.

## Agent runtime
`START → LOAD_RECORDS → VALIDATE → DECIDE → COMPLETE | HUMAN_REVIEW | FAILED`

The state machine performs deterministic checks. The allowlisted `ToolExecutor` validates tool name and arguments with Pydantic before tenant-scoped data access. The MCP process delegates to the same authenticated HTTP API.

## Retrieval and memory
Policy retrieval combines token overlap and character n-gram similarity. This is a reproducible lexical/fuzzy baseline; it is not a neural embedding model. Episodic/semantic/procedural memory records are tenant-scoped and ranked by deterministic token overlap. The service interfaces can be extended with a sentence-transformer embedding provider and pgvector index after benchmarking.

## Inference
Ollama and vLLM adapters can explain already-computed findings. They are not used to compute the financial decision. Provider calls have a bounded timeout and return an error if not configured.

## Tenant boundaries
All business records include tenant_id and API queries filter by the authenticated user's tenant. Before production, enforce PostgreSQL RLS and use a non-owner role; application filters alone are not a complete defense against every query bug.

## Data flow and trust boundaries
- Uploaded files are untrusted input.
- Extracted fields are candidates, never trusted facts until user confirmation.
- Retrieved policy text is evidence, not executable instructions.
- Model output is untrusted explanatory text.
- Only authorized human reviewers can finalize invoice decisions.
