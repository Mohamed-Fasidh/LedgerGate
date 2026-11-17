from __future__ import annotations

import torch
from torch import nn
from torch.distributions import Categorical


class CategoricalPolicy(nn.Module):
    """MLP categorical policy: state [B, obs_dim] -> logits [B, action_dim]."""

    def __init__(self, obs_dim: int, action_dim: int, hidden_dim: int = 64):
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(obs_dim, hidden_dim), nn.Tanh(),
            nn.Linear(hidden_dim, hidden_dim), nn.Tanh(),
            nn.Linear(hidden_dim, action_dim),
        )

    def forward(self, states: torch.Tensor) -> torch.Tensor:
        return self.network(states)

    def act(self, states: torch.Tensor, deterministic: bool = False):
        logits = self(states)
        distribution = Categorical(logits=logits)
        actions = logits.argmax(-1) if deterministic else distribution.sample()
        return actions, distribution.log_prob(actions), distribution.entropy()


class ActorCritic(nn.Module):
    """Shared MLP trunk with separate policy logits and scalar value head."""

    def __init__(self, obs_dim: int, action_dim: int, hidden_dim: int = 64):
        super().__init__()
        self.trunk = nn.Sequential(
            nn.Linear(obs_dim, hidden_dim), nn.Tanh(),
            nn.Linear(hidden_dim, hidden_dim), nn.Tanh(),
        )
        self.policy_head = nn.Linear(hidden_dim, action_dim)
        self.value_head = nn.Linear(hidden_dim, 1)

    def forward(self, states: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        hidden = self.trunk(states)
        return self.policy_head(hidden), self.value_head(hidden).squeeze(-1)

    def act(self, states: torch.Tensor, deterministic: bool = False):
        logits, values = self(states)
        distribution = Categorical(logits=logits)
        actions = logits.argmax(-1) if deterministic else distribution.sample()
        return actions, distribution.log_prob(actions), distribution.entropy(), values
