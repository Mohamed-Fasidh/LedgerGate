# LedgerGate AI Engineering Research Suite

This directory contains optional, standalone research and teaching packages that complement LedgerGate's enterprise reconciliation application. These packages are deliberately isolated from the financial transaction runtime.

## Planned package layout

- `transformer/` — decoder-only Transformer, byte-level tokenizer, training, checkpoints, and generation.
- `reinforcement-learning/` — REINFORCE, actor-critic, PPO/GAE, and GRPO teaching implementations.
- `inference/` — inference client, request metrics, benchmark harness, Prometheus exposition, and vLLM profiling helper.
- `agent-evaluation/` — trace datasets, evaluation runner, security/financial/retrieval metrics, and comparison reports.
- `instructor-workshops/` — four instructor-led lessons, code labs, and facilitator materials.

## Safety and integration boundary

The research packages are educational and experimental. They must not make payment decisions, bypass human approval, or perform autonomous writes to LedgerGate's financial records. LedgerGate's deterministic reconciliation rules, tenant authorization, audit trail, and human approval workflow remain authoritative.

## Validation status

Local package-level test results reported with the source bundle: **88 tests passed**. These are not evidence that the full LedgerGate repository CI, a GPU benchmark, an external model server, staging deployment, or production security review has passed. Those checks must be run independently after all source files have been integrated.

## Running package tests

Run `python -m pytest -q` from an individual package root. The inference tests are under `inference/backend`; the workshop lab tests are under `instructor-workshops`.

## Dependencies

Each package has its own dependency declaration. Install dependencies only for the package being explored; do not merge research-only dependencies into the production application requirements without a separate review.


## Teaching workshops

Instructor-led 15–20 minute workshop plans are maintained in [docs/workshops](../docs/workshops/README.md), covering Transformers, agent engineering/evaluation, PPO/GRPO, and inference/GPU performance.
