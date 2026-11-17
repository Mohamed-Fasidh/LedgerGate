# Policy gradients, GAE, PPO and GRPO

## Markov decision process

An episodic task is described by states (s_t), actions (a_t), rewards (r_t), transition dynamics and a discount factor (\gamma\in[0,1]). The discounted return from time (t) is:

\[
G_t=\sum_{k=0}^{T-t-1}\gamma^k r_{t+k}
\]

For a terminal boundary, the return recursion resets. The implementation's `discounted_returns` function uses the `done` mask to avoid leaking rewards from one episode into another.

## REINFORCE

A stochastic policy (\pi_\theta(a\mid s)) maximizes expected return. The score-function estimator gives the loss minimized by gradient descent:

\[
\mathcal L_{PG}=-\sum_t \log\pi_\theta(a_t\mid s_t)G_t
\]

A baseline independent of the sampled action can reduce variance without changing the expected policy gradient. This lab's REINFORCE implementation normalizes within-episode returns when their variance is non-zero; it does not use a learned value function.

## Actor–critic

The actor produces a categorical action distribution. The critic estimates (V_\phi(s)). A simple advantage estimate is:

\[
\hat A_t=G_t-V_\phi(s_t)
\]

The actor loss is the negative log probability weighted by the detached advantage. The critic minimizes squared error to the return target. An entropy bonus encourages exploration:

\[
\mathcal L=\mathcal L_{actor}+c_v\mathcal L_{critic}-c_e\mathcal H(\pi)
\]

## Generalized Advantage Estimation

For value predictions (V_t), the temporal-difference residual is:

\[
\delta_t=r_t+\gamma(1-d_t)V_{t+1}-V_t
\]

GAE exponentially weights future TD residuals:

\[
\hat A_t=\delta_t+\gamma\lambda(1-d_t)\hat A_{t+1}
\]

The value target is (\hat R_t=\hat A_t+V_t). Here (d_t=1) marks an episode boundary, so bootstrapping and GAE recursion stop there. For truncated non-terminal episodes, a correct production rollout collector should preserve and bootstrap the final observation; this compact LineWorld implementation intentionally treats both termination and truncation as rollout boundaries for the finite horizon.

## Proximal Policy Optimization

The ratio between updated and behavior policy probabilities is:

\[
r_t(\theta)=\frac{\pi_\theta(a_t\mid s_t)}{\pi_{old}(a_t\mid s_t)}
=\exp(\log\pi_\theta-\log\pi_{old})
\]

PPO clips the surrogate objective:

\[
\mathcal L_{clip}=-\mathbb E_t\left[
\min\left(r_t\hat A_t,
\operatorname{clip}(r_t,1-\epsilon,1+\epsilon)\hat A_t\right)
\right]
\]

The implementation also fits the critic to GAE value targets and tracks entropy and clip fraction.

## Group Relative Policy Optimization (GRPO)

For prompt (q), sample a group of (G) candidate responses and score them with a reward function. Relative advantages are calculated within each prompt group:

\[
\hat A_{i,j}=\frac{R_{i,j}-\operatorname{mean}(R_i)}
{\operatorname{std}(R_i)+\varepsilon}
\]

The response-level clipped surrogate has the same ratio form as PPO, but the advantage comes from peer candidates for the same prompt rather than a learned value baseline. A real LLM implementation needs token-level log probabilities, response masks, careful sequence-length handling, reference-policy or KL control, distributed rollout design, and validated reward evaluation. Those features are outside this lab's synthetic discrete-choice example.

## What to measure

Track learning curves and held-out greedy evaluation, not only the training reward. For control tasks, report return and success rate. For the synthetic GRPO example, report group reward and content-choice accuracy, while clearly labeling the fixed rubric. Repeat experiments across seeds before making comparative claims.
