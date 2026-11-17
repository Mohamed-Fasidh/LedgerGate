# PPO versus GRPO

Both methods use a clipped likelihood-ratio surrogate, but their advantage estimates differ.

| Aspect | PPO in this lab | GRPO in this lab |
|---|---|---|
| Rollout unit | State/action transitions | Prompt/response-slot candidates |
| Baseline | Learned critic (V(s)) | Mean reward within each prompt's group |
| Advantage | GAE, then normalized | Group-relative reward normalization |
| Optimization | Multiple minibatch epochs | Multiple updates over sampled candidates |
| Evaluation | Greedy LineWorld success/return | Greedy content-choice accuracy/reward |
| Scope | Small discrete control task | Synthetic fixed phrase choices |

PPO learns a value estimator from its rollout data. GRPO avoids a separate critic in the shown group-relative objective by comparing multiple candidates for the same prompt. This does not make the shown GRPO implementation equivalent to production LLM training.

## Important limitations

The GRPO policy chooses three discrete slots: a response prefix, one content phrase and an ending. It does not tokenize, generate, score or optimize a natural-language model. The task rubric checks a predeclared set of correct phrase IDs. It does not measure semantic correctness, reasoning, helpfulness or safety. The experiment is a teaching illustration of grouped sampling and relative advantages only.

For real LLM GRPO, one would need prompt/response tokenization, per-token old/new log probabilities, response masks, sequence-level reward assignment, normalization policy, KL/reference model handling, stable batching, distributed rollout collection, checkpoint/recovery, and independent reward validation. Those requirements must not be inferred from this toy implementation.
