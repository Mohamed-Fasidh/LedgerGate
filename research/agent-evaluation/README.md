# LedgerGate Agent Evaluation

A deterministic evaluation harness for agent state traces, tool selection and arguments, hybrid retrieval, evidence-reference integrity, failure recovery, multi-step execution, financial arithmetic, authorization fixtures, and optional live backend contracts.

## Install

From this directory:

```bash
python -m pip install -e ".[test]"
```

## Validate the bundled dataset

```bash
python -m eval_framework.cli validate-dataset
```

The default dataset is datasets/v1. It is a small, synthetic, versioned contract fixture set. It is not a representative production workload and its scores must not be described as production model accuracy.

## Run the deterministic evaluation

```bash
python -m eval_framework.cli run --ci --model-version synthetic-fixtures --output-dir outputs/local
```

The run exports:
- run.json: environment, dataset version, configuration, metrics, and limitations.
- case_results.csv: per-case expected and observed outcomes.
- failure_analysis.json: grouped deterministic failures.
- human_review_queue.jsonl: cases requiring human review.
- benchmark_report.md: readable report.

The CI mode requires the deterministic reference gates to pass. Live backend and PostgreSQL RLS tests are not implied by offline fixtures.

## Evaluate actual agent telemetry

Provide JSONL traces matching schemas/agent_trace.schema.json:

```bash
python -m eval_framework.cli run \
  --dataset-dir datasets/v1 \
  --output-dir outputs/observed-run \
  --model-version YOUR_MODEL_VERSION \
  --trace-file /path/to/agent-traces.jsonl
```

Trace IDs must be unique in the supplied file. Use a single clock domain for started_at_ms and completed_at_ms. Populate tool_calls, claims, expected/observed steps, and recovery observations only when those observations are available. Missing production telemetry is reported as not_measured; it is not replaced with fixture scores.

## Optional live integration

Pass --ledgergate-root pointing to a local LedgerGate checkout to run the live reconciliation, tool/retrieval contracts, and PostgreSQL RLS inventory adapter. Supply --postgres-url only for a dedicated test database. Never point evaluation integration tests at production financial data.

## Metric interpretation

- Task completion, tool selection, and argument exact-match are computed from supplied traces or explicitly labelled fixtures.
- Retrieval precision/recall/MRR/NDCG use the included synthetic corpus unless live SQL retrieval is enabled.
- Claim evidence support checks citation-ID integrity; it does not establish semantic entailment or a complete hallucination rate.
- Financial totals are a Decimal reference oracle, not a live invoice API.
- Authorization fixtures validate the reference policy matrix; they do not replace application route authorization and PostgreSQL RLS tests.
- LLM-as-judge is supplementary and must not override deterministic tests or human review.

## Tests

```bash
python -m pytest -q
```

GitHub Actions runs dataset validation, unit tests, and deterministic CI gates on relevant pull requests and main-branch changes.
