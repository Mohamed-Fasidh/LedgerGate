# PPO and GRPO

**Duration:** 18 minutes

## Learning objectives
Explain policy gradients, advantage estimation, PPO clipping, group-relative reward normalization, and the limitations of small educational GRPO implementations.

## Prerequisites
Probability distributions, log probabilities, gradient descent, and basic reinforcement learning.

## Mathematics and whiteboard
For policy pi_theta(a|s), a policy-gradient objective weights log probability by advantage: J(theta)=E[log pi_theta(a|s) A(s,a)]. Define the TD residual delta_t=r_t+gamma V(s_{t+1})−V(s_t). GAE forms Ahat_t=sum_l (gamma lambda)^l delta_{t+l}. PPO defines ratio r_t(theta)=exp(log pi_theta(a_t|s_t)−log pi_old(a_t|s_t)) and maximizes E[min(r_t Ahat_t, clip(r_t,1−epsilon,1+epsilon) Ahat_t)]. In simplified group-relative optimization, sample several candidates for one prompt and normalize their rewards within that prompt group: A_i=(R_i−mean(R))/(std(R)+epsilon).

## Timed teaching script
- 0–3 min: policy, trajectory, return, and advantage.
- 3–6 min: TD residuals and GAE intuition.
- 6–9 min: PPO probability ratio and clipped objective.
- 9–12 min: group sampling and within-prompt reward normalization.
- 12–16 min: inspect the repository PPO and GRPO modules and focused tests.
- 16–18 min: explain why synthetic reward is not evidence of LLM reasoning.

## Coding demonstration
From research/reinforcement-learning, install pip install -e '.[test]' and run python -m pytest -q. Inspect ppo_clipped_objective, GAE use, normalize_group_rewards, and grpo_clipped_objective. Run the CPU configuration and inspect actual tracker output.

## Common mistakes
- Confusing reward with advantage.
- Failing to mask terminal transitions in GAE.
- Comparing old and new log probabilities for different sampled actions.
- Forgetting to detach old-policy log probabilities.
- Normalizing across prompts instead of within each group.
- Calling a discrete response-slot policy a full token-level LLM GRPO implementation.

## Applications
Policy optimization for control, reward-guided model-training research, and reproducible optimization experiments.

## Interview questions
1. What does GAE lambda trade off?
2. Why does PPO use the minimum of clipped and unclipped objectives?
3. What does a probability ratio above one mean?
4. Why normalize rewards within each prompt group?
5. What is needed to move from a toy policy to token-level language-model optimization?

## Student exercise
For four candidate rewards, calculate the mean, population standard deviation, and normalized rewards. Add a test for approximately zero group mean and finite normalized values.

## Instructor note
The repository's GRPO experiment is educational and response-slot based. It does not reproduce production-scale LLM training or DeepSeek-R1.
