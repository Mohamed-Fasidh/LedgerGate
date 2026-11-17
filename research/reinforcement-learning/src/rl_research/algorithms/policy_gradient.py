from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from torch.distributions import Categorical

from rl_research.envs import LineWorld
from rl_research.models import CategoricalPolicy
from rl_research.tracking import ExperimentTracker
from rl_research.utils import choose_device, discounted_returns, set_seed


def evaluate(
    policy: CategoricalPolicy,
    episodes: int = 30,
    device: torch.device | None = None,
) -> dict:
    if episodes < 1:
        raise ValueError("episodes must be positive")
    device = device or next(policy.parameters()).device
    env = LineWorld()
    returns, successes = [], 0
    policy.eval()
    for _ in range(episodes):
        observation, _ = env.reset()
        total = 0.0
        for _ in range(env.max_steps):
            state = torch.as_tensor(
                observation, dtype=torch.float32, device=device
            ).unsqueeze(0)
            action = int(policy(state).argmax(-1).item())
            observation, reward, terminated, truncated, info = env.step(action)
            total += reward
            if terminated or truncated:
                successes += int(info["success"])
                break
        returns.append(total)
    policy.train()
    return {
        "eval_return": float(np.mean(returns)),
        "success_rate": successes / episodes,
    }


def train(config: dict, output_dir: str | Path, device_name: str = "auto") -> dict:
    set_seed(int(config["seed"]))
    device = choose_device(device_name)
    env = LineWorld()
    policy = CategoricalPolicy(
        env.observation_size, env.action_size, config["hidden_dim"]
    ).to(device)
    optimizer = torch.optim.Adam(policy.parameters(), lr=config["learning_rate"])
    tracker = ExperimentTracker(output_dir, "policy_gradient", config)
    episode_returns = []

    for episode in range(1, config["episodes"] + 1):
        observation, _ = env.reset()
        log_probs, rewards = [], []
        total = 0.0
        for _ in range(env.max_steps):
            state = torch.as_tensor(
                observation, dtype=torch.float32, device=device
            ).unsqueeze(0)
            distribution = Categorical(logits=policy(state))
            action = distribution.sample()
            log_probs.append(distribution.log_prob(action).squeeze(0))
            observation, reward, terminated, truncated, _ = env.step(int(action.item()))
            rewards.append(reward)
            total += reward
            if terminated or truncated:
                break

        returns = discounted_returns(rewards, config["gamma"], device=device)
        # Standardizing one episode's returns reduces scale sensitivity; no
        # learned value baseline is used, so this remains a REINFORCE example.
        weights = (
            (returns - returns.mean()) / (returns.std(unbiased=False) + 1e-8)
            if returns.numel() > 1 and returns.std(unbiased=False).item() > 1e-8
            else returns
        )
        loss = -(torch.stack(log_probs) * weights).sum()
        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(policy.parameters(), 1.0)
        optimizer.step()
        episode_returns.append(total)

        metrics = {
            "episode": episode,
            "episode_return": total,
            "episode_length": len(rewards),
            "policy_loss": float(loss.item()),
        }
        if episode % config["eval_interval"] == 0 or episode == config["episodes"]:
            metrics.update(evaluate(policy, config["eval_episodes"], device))
        tracker.log(**metrics)

    summary = evaluate(policy, config["eval_episodes"], device)
    torch.save(
        {"model": policy.state_dict(), "config": config, "algorithm": "policy_gradient"},
        Path(tracker.directory) / "checkpoint.pt",
    )
    tracker.close(summary)
    return summary | {"last_20_episode_return": float(np.mean(episode_returns[-20:]))}
