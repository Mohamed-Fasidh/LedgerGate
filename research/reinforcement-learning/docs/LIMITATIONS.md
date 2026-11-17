# Limitations and safe use

1. **Environment scale.** LineWorld is a deterministic seven-position toy task with two actions. Results do not establish robust robotics, game-playing, navigation or real-world control.
2. **Episode boundaries.** The finite-horizon examples stop the return/GAE recursion at both termination and truncation. A general environment should bootstrap value at time-limit truncation when the underlying episode is not terminal.
3. **REINFORCE normalization.** Returns are normalized within an episode when possible. This changes optimization scale and should be included in experiment descriptions.
4. **Actor–critic.** The implementation uses Monte-Carlo returns rather than a bootstrapped n-step target. It is a compact baseline, not a complete modern actor–critic framework.
5. **PPO rollout.** This example collects a small on-policy batch and reuses it for a few epochs. It omits vectorized environments, observation normalization, recurrent policies, timeout bootstrapping and distributed collection.
6. **GRPO.** The policy selects fixed response components and uses a transparent synthetic reward. It is not an LLM, not token-level GRPO and not evidence of reasoning or semantic evaluation.
7. **Determinism.** Seeds improve repeatability but cannot promise identical results across accelerators, drivers or PyTorch releases.
8. **Checkpoint trust.** Checkpoints are local experiment artifacts. Only load checkpoint files from trusted sources.
9. **No production integration.** Do not connect the code to LedgerGate's financial workflow. These policies must never approve, settle, refund, or directly mutate invoices or payment records.

Use the package to learn algorithm mechanics, test equations, and practice experiment hygiene. Do not present toy benchmark results as production performance or research-grade generalization.
