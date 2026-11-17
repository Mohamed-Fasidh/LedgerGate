from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch.distributions import Categorical

from rl_research.envs import LineWorld
from rl_research.models import ActorCritic
from rl_research.tracking import ExperimentTracker
from rl_research.utils import choose_device, discounted_returns, set_seed


def evaluate(
    model: ActorCritic,
    episodes: int = 30,
    device: torch.device | None = None,
) -> dict:
    if episodes < 1:
        raise ValueError("episodes must be positive")
    device = device or next(model.parameters()).device
    env = LineWorld()
    scores, successes = [], 0
    model.eval()
    for _ in range(episodes):
        observation, _ = env.reset()
        total = 0.0
        for _ in range(env.max_steps):
            state = torch.as_tensor(
                observation, dtype=torch.float32, device=device
            ).unsqueeze(0)
            logits, _ = model(state)
            action = int(logits.argmax(-1).item())
            observation, reward, terminated, truncated, info = env.step(action)
            total += reward
            if terminated or truncated:
                successes += int(info["success"])
                break
        scores.append(total)
    model.train()
    return {
        "eval_return": float(np.mean(scores)),
        "success_rate": successes / episodes,
    }


def train(config: dict, output_dir: str | Path, device_name: str = "auto") -> dict:
    set_seed(int(config["seed"]))
    device = choose_device(device_name)
    env = LineWorld()
    model = ActorCritic(
        env.observation_size, env.action_size, config["hidden_dim"]
    ).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=config["learning_rate"])
    tracker = ExperimentTracker(output_dir, "actor_critic", config)
    returns_log = []

    for episode in range(1, config["episodes"] + 1):
        observation, _ = env.reset()
        log_probs, values, rewards, entropies = [], [], [], []
        total = 0.0
        for _ in range(env.max_steps):
            state = torch.as_tensor(
                observation, dtype=torch.float32, device=device
            ).unsqueeze(0)
            logits, value = model(state)
            distribution = Categorical(logits=logits)
            action = distribution.sample()
            log_probs.append(distribution.log_prob(action).squeeze(0))
            values.append(value.squeeze(0))
            entropies.append(distribution.entropy().squeeze(0))
            observation, reward, terminated, truncated, _ = env.step(int(action.item()))
            rewards.append(reward)
            total += reward
            if terminated or truncated:
                break

        targets = discounted_returns(rewards, config["gamma"], device=device)
        value_tensor = torch.stack(values)
        advantages = targets - value_tensor.detach()
        policy_loss = -(torch.stack(log_probs) * advantages).mean()
        value_loss = F.mse_loss(value_tensor, targets)
        entropy = torch.stack(entropies).mean()
        loss = (
            policy_loss
            + config["value_coef"] * value_loss
            - config["entropy_coef"] * entropy
        )
        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        returns_log.append(total)

        metrics = {
            "episode": episode,
            "episode_return": total,
            "episode_length": len(rewards),
            "actor_loss": float(policy_loss.item()),
            "critic_loss": float(value_loss.item()),
            "entropy": float(entropy.item()),
        }
        if episode % config["eval_interval"] == 0 or episode == config["episodes"]:
            metrics.update(evaluate(model, config["eval_episodes"], device))
        tracker.log(**metrics)

    summary = evaluate(model, config["eval_episodes"], device)
    torch.save(
        {"model": model.state_dict(), "config": config, "algorithm": "actor_critic"},
        Path(tracker.directory) / "checkpoint.pt",
    )
    tracker.close(summary)
    return summary | {"last_20_episode_return": float(np.mean(returns_log[-20:]))}
