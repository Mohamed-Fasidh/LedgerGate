# Research plan: reliable financial agents

## Research question
Can structured memory, evidence-grounded tool execution, and evaluation-driven optimization improve enterprise agent reliability while reducing inference cost?

## Versioned benchmark scenarios
- Exact invoice/PO match.
- Overbilled and underbilled invoice.
- Currency mismatch.
- Missing PO.
- Supplier mismatch.
- Duplicate invoice identifier.
- Missing extracted fields.
- Malformed tool arguments and unknown tool.
- Tool timeout and model-provider unavailable cases.

## Baselines and ablations
1. Deterministic rules vs rules plus policy retrieval.
2. Token overlap vs hybrid lexical/fuzzy retrieval.
3. No memory vs tenant-scoped structured memory.
4. Direct tool calls vs allowlisted schema-validated execution.
5. Fixed local model vs vLLM/Ollama provider configuration.
6. No retry vs bounded retry/idempotency (future worker implementation).

## Metrics
Task completion, precision/recall for discrepancy flags, evidence attribution, tool argument validity, cross-tenant denial rate, duplicate action rate, recovery rate, p50/p95 latency, tokens/task, and cost/successful task.

Pin dataset, model, prompts, commit, hardware, and run configuration. Report failures and confidence intervals. The bundled 5-case suite is only a deterministic smoke benchmark; do not present its 100% pass rate as general model accuracy.
