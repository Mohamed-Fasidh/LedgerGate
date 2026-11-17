from __future__ import annotations

import random
from typing import Iterable

import numpy as np
import torch


def set_seed(seed: int) -> None:
    """Seed Python, NumPy and PyTorch for repeatable local experiments."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    # Deterministic algorithms can be slower; this tiny MLP does not need them.
    torch.use_deterministic_algorithms(False)


def discounted_returns(
    rewards: Iterable[float] | torch.Tensor,
    gamma: float,
    dones: Iterable[bool] | torch.Tensor | None = None,
    *,
    device: torch.device | str | None = None,
) -> torch.Tensor:
    """Compute Monte-Carlo returns, resetting the recursion at episode ends."""
    reward_tensor = torch.as_tensor(
        list(rewards) if not isinstance(rewards, torch.Tensor) else rewards,
        dtype=torch.float32,
        device=device,
    )
    done_tensor = torch.zeros_like(reward_tensor) if dones is None else torch.as_tensor(
        list(dones) if not isinstance(dones, torch.Tensor) else dones,
        dtype=torch.float32,
        device=reward_tensor.device,
    )
    if reward_tensor.ndim != 1 or done_tensor.shape != reward_tensor.shape:
        raise ValueError("rewards and dones must be one-dimensional tensors of equal length")
    returns = torch.zeros_like(reward_tensor)
    running = torch.zeros((), dtype=reward_tensor.dtype, device=reward_tensor.device)
    for timestep in reversed(range(len(reward_tensor))):
        running = reward_tensor[timestep] + gamma * running * (1.0 - done_tensor[timestep])
        returns[timestep] = running
    return returns


def compute_gae(
    rewards: torch.Tensor,
    values: torch.Tensor,
    dones: torch.Tensor,
    bootstrap_value: float | torch.Tensor = 0.0,
    gamma: float = 0.99,
    gae_lambda: float = 0.95,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Generalized Advantage Estimation.

    Inputs have shape [T]. `dones[t]` masks bootstrap across a terminal/truncated
    episode boundary. Returns (advantages, value_targets), each shaped [T].
    """
    rewards = torch.as_tensor(rewards, dtype=torch.float32)
    values = torch.as_tensor(values, dtype=torch.float32, device=rewards.device)
    dones = torch.as_tensor(dones, dtype=torch.float32, device=rewards.device)
    if rewards.ndim != 1 or values.shape != rewards.shape or dones.shape != rewards.shape:
        raise ValueError("rewards, values and dones must be 1-D tensors with the same shape")
    advantages = torch.zeros_like(rewards)
    next_value = torch.as_tensor(bootstrap_value, dtype=torch.float32, device=rewards.device)
    gae = torch.zeros((), dtype=torch.float32, device=rewards.device)
    for timestep in reversed(range(rewards.numel())):
        nonterminal = 1.0 - dones[timestep]
        delta = rewards[timestep] + gamma * next_value * nonterminal - values[timestep]
        gae = delta + gamma * gae_lambda * nonterminal * gae
        advantages[timestep] = gae
        next_value = values[timestep]
    return advantages, advantages + values


def normalize_advantages(advantages: torch.Tensor, eps: float = 1e-8) -> torch.Tensor:
    """Zero-center and scale advantages using population standard deviation."""
    if advantages.numel() == 0:
        raise ValueError("advantages cannot be empty")
    return (advantages - advantages.mean()) / (advantages.std(unbiased=False) + eps)


def choose_device(requested: str = "auto") -> torch.device:
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available in this PyTorch runtime")
    return torch.device(requested)
