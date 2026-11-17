from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch.distributions import Categorical

from rl_research.envs import LineWorld
from rl_research.models import ActorCritic
from rl_research.tracking import ExperimentTracker
from rl_research.utils import (
    choose_device,
    compute_gae,
    normalize_advantages,
    set_seed,
)


def ppo_clipped_objective(
    new_log_probs: torch.Tensor,
    old_log_probs: torch.Tensor,
    advantages: torch.Tensor,
    clip_epsilon: float = 0.2,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Return PPO's clipped surrogate loss and the per-sample probability ratios."""
    if not 0.0 <= clip_epsilon < 1.0:
        raise ValueError("clip_epsilon must be in [0, 1)")
    ratios = torch.exp(new_log_probs - old_log_probs)
    unclipped = ratios * advantages
    clipped = torch.clamp(
        ratios, 1.0 - clip_epsilon, 1.0 + clip_epsilon
    ) * advantages
    return -torch.minimum(unclipped, clipped).mean(), ratios


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
    tracker = ExperimentTracker(output_dir, "ppo", config)

    for update in range(1, config["updates"] + 1):
        states, actions, old_log_probs = [], [], []
        rewards, dones, values = [], [], []
        episode_scores = []
        for _ in range(config["episodes_per_update"]):
            observation, _ = env.reset()
            score = 0.0
            for _ in range(env.max_steps):
                state = torch.as_tensor(observation, dtype=torch.float32, device=device)
                with torch.no_grad():
                    logits, value = model(state.unsqueeze(0))
                    distribution = Categorical(logits=logits)
                    action = distribution.sample()
                    log_prob = distribution.log_prob(action)
                next_observation, reward, terminated, truncated, _ = env.step(
                    int(action.item())
                )
                done = terminated or truncated
                states.append(state)
                actions.append(action.squeeze(0))
                old_log_probs.append(log_prob.squeeze(0))
                values.append(value.squeeze(0))
                rewards.append(float(reward))
                dones.append(float(done))
                score += reward
                observation = next_observation
                if done:
                    break
            episode_scores.append(score)

        state_tensor = torch.stack(states)
        action_tensor = torch.stack(actions).long()
        old_log_prob_tensor = torch.stack(old_log_probs).detach()
        value_tensor = torch.stack(values).detach()
        reward_tensor = torch.tensor(rewards, dtype=torch.float32, device=device)
        done_tensor = torch.tensor(dones, dtype=torch.float32, device=device)
        advantages, value_targets = compute_gae(
            reward_tensor,
            value_tensor,
            done_tensor,
            0.0,
            config["gamma"],
            config["gae_lambda"],
        )
        advantages = normalize_advantages(advantages)
        losses, clip_fractions, entropies, value_losses = [], [], [], []
        indices = torch.arange(state_tensor.shape[0], device=device)
        for _ in range(config["update_epochs"]):
            permutation = indices[torch.randperm(indices.numel(), device=device)]
            for start in range(0, len(permutation), config["minibatch_size"]):
                minibatch = permutation[start : start + config["minibatch_size"]]
                logits, predicted_values = model(state_tensor[minibatch])
                distribution = Categorical(logits=logits)
                new_log_prob = distribution.log_prob(action_tensor[minibatch])
                actor_loss, ratios = ppo_clipped_objective(
                    new_log_prob,
                    old_log_prob_tensor[minibatch],
                    advantages[minibatch],
                    config["clip_epsilon"],
                )
                critic_loss = F.mse_loss(
                    predicted_values, value_targets[minibatch]
                )
                entropy = distribution.entropy().mean()
                loss = (
                    actor_loss
                    + config["value_coef"] * critic_loss
                    - config["entropy_coef"] * entropy
                )
                optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 0.5)
                optimizer.step()
                losses.append(float(loss.item()))
                clip_fractions.append(
                    float(
                        ((ratios - 1.0).abs() > config["clip_epsilon"])
                        .float().mean().item()
                    )
                )
                entropies.append(float(entropy.item()))
                value_losses.append(float(critic_loss.item()))

        metrics = {
            "update": update,
            "rollout_return": float(np.mean(episode_scores)),
            "policy_value_loss": float(np.mean(losses)),
            "critic_loss": float(np.mean(value_losses)),
            "entropy": float(np.mean(entropies)),
            "clip_fraction": float(np.mean(clip_fractions)),
            "rollout_steps": len(states),
        }
        if update % config["eval_interval"] == 0 or update == config["updates"]:
            metrics.update(evaluate(model, config["eval_episodes"], device))
        tracker.log(**metrics)

    summary = evaluate(model, config["eval_episodes"], device)
    torch.save(
        {"model": model.state_dict(), "config": config, "algorithm": "ppo"},
        Path(tracker.directory) / "checkpoint.pt",
    )
    tracker.close(summary)
    return summary | {
        "last_rollout_return": float(
            np.mean([row["rollout_return"] for row in tracker.rows[-10:]])
        )
    }
