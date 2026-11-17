# Reinforcement Learning Research Lab

A self-contained PyTorch lab for understanding and comparing policy-gradient methods. It complements LedgerGate's enterprise AI application code but is intentionally isolated from the financial transaction runtime.

## Algorithms

| Method | Implementation | What it demonstrates |
|---|---|---|
| REINFORCE | `algorithms/policy_gradient.py` | Monte-Carlo returns and score-function policy gradients |
| Actor–critic | `algorithms/actor_critic.py` | A learned value baseline, policy/value losses, and entropy regularization |
| PPO | `algorithms/ppo.py` | Rollout collection, GAE, normalized advantages, clipped policy objective, minibatches |
| GRPO (toy) | `algorithms/grpo.py` | Multiple candidates per prompt, per-prompt group-relative reward normalization, clipped updates |

The first three algorithms interact with the deterministic `LineWorld` navigation environment. GRPO uses a tiny prompt-conditioned categorical model that selects a prefix, a content phrase and an ending from fixed lists. **It is not a language model, not autoregressive generation, not token-level GRPO and not a model fine-tuning demonstration.** It exists to make grouped sampling and relative reward normalization inspectable.

## Install

From this directory:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
python -m pytest -q
```

To create experiment plots, install the optional Matplotlib dependency (included in the `test` extra):

```bash
python -m pip install -e ".[research]"
```

## Run experiments on CPU

Run all four compact experiments:

```bash
python -m rl_research.run_all --only all --device cpu --output outputs --threads 1
```

Run one method at a time:

```bash
python -m rl_research.run_all --only policy_gradient --device cpu
python -m rl_research.run_all --only actor_critic --device cpu
python -m rl_research.run_all --only ppo --device cpu
python -m rl_research.run_all --only grpo --device cpu
```

Configuration files and the synthetic GRPO prompt set are installed as package data. The runner records Python, PyTorch, NumPy, device and thread metadata. Each experiment directory contains a checkpoint, machine-readable JSON and CSV metrics, and a summary. The combined run creates training-curve figures.

## Reproducibility

- Experiments seed Python, NumPy and PyTorch.
- Evaluation uses greedy actions on separate deterministic episodes.
- Configurations are stored as JSON and embedded in checkpoints.
- The runner writes environment metadata to `run_metadata.json`.
- Use the same device, framework version, seed and configuration when comparing experiments.

A fixed seed makes a run repeatable within a compatible software/hardware setup; it does not guarantee bit-for-bit determinism on every accelerator or across PyTorch versions.

## Interpretation

`LineWorld` has a short horizon and a tiny state/action space. A high success rate demonstrates that the policy learned this small task, not that it generalizes to realistic robotics or control. GRPO rewards come from an explicit synthetic rubric that checks a content-choice ID and two simple style preferences; they are not human judgments or semantic model evaluations.

The included defaults are compact CPU teaching runs. Change one configuration variable at a time and report evaluation returns, success rate, training curves and compute metadata. Do not compare methods using different environment budgets without saying so.

## Safety and scope

These algorithms must not be connected to LedgerGate's invoice, settlement, approval or payment pathways. They are offline research exercises. No model in this directory is allowed to approve financial transactions or modify production records. Tenant authorization, deterministic business rules, audit events and human approval remain authoritative in the main application.
