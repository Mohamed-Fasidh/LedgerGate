# LedgerGate AI Engineering Research Suite

This directory contains optional research packages that complement LedgerGate's enterprise reconciliation application. Research code is deliberately isolated from the financial transaction runtime.

## Implemented packages

- `transformer/` — GPT-style decoder-only Transformer, byte-level tokenizer, CPU training, checkpoints, generation, and unit tests.
- `reinforcement-learning/` — REINFORCE, actor-critic, PPO/GAE, and an educational response-slot GRPO experiment.
- `agent-evaluation/` — versioned deterministic trace datasets, evaluator CLI, financial/security/retrieval metrics, comparison reports, human-review queue, and optional live backend contracts.

## Supporting engineering components

- Inference client and benchmark harness live in `backend/app/services/inference/`; optional dependencies are in `backend/requirements-inference.txt`. The vLLM launch-profile helper is `scripts/vllm_profile.py`.
- Four Vizuara teaching plans live in `docs/workshops/`; they are instructor materials rather than a separate Python package.

## Safety and integration boundary

The research packages are educational and experimental. They must not make payment decisions, bypass human approval, or perform autonomous writes to LedgerGate's financial records. LedgerGate's deterministic reconciliation rules, tenant authorization, audit trail, and human approval workflow remain authoritative.

## Validation status

The agent-evaluation package's dedicated GitHub Actions run validated the versioned dataset, ran package tests, and passed deterministic CI gates (run [44082141985](https://github.com/Mohamed-Fasidh/LedgerGate/actions/runs/44082141985)). The research Transformer and RL packages have separate CPU-oriented test workflows. These checks do not represent production model accuracy, GPU performance, external model-server integration, staging deployment, or production security review.

## Running package tests

From each package root:

```bash
python -m pip install -e ".[test]"
python -m pytest -q
```

For the agent evaluation package, run the bundled deterministic suite:

```bash
python -m eval_framework.cli validate-dataset
python -m eval_framework.cli run --ci --output-dir outputs/local
```

The bundled evaluation fixtures are synthetic contract cases. Their pass rates must not be presented as production LLM-agent accuracy. Actual model-serving metrics remain `not_measured` until real trace files are supplied.

## Dependencies

Each package has its own dependency declaration. Install dependencies only for the package being explored; do not merge research-only dependencies into production application requirements without a separate review.
