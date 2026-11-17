import pytest
import torch

from rl_research.envs import LineWorld
from rl_research.models import ActorCritic, CategoricalPolicy


def test_lineworld_terminal_and_reward():
    env = LineWorld(n_positions=3, max_steps=5)
    obs, _ = env.reset()
    assert obs.shape == (3,) and obs[0] == 1
    obs, reward, terminated, truncated, info = env.step(1)
    assert reward == pytest.approx(0.01) and not terminated and not truncated
    obs, reward, terminated, truncated, info = env.step(1)
    assert reward == 1.0 and terminated and info["success"]


def test_lineworld_truncates_at_horizon():
    env = LineWorld(n_positions=5, max_steps=1)
    env.reset()
    _, _, terminated, truncated, _ = env.step(0)
    assert not terminated and truncated


def test_categorical_policy_shapes_and_sampled_actions():
    model = CategoricalPolicy(7, 2, 16)
    actions, logp, entropy = model.act(torch.randn(5, 7))
    assert actions.shape == logp.shape == entropy.shape == (5,)
    assert actions.min() >= 0 and actions.max() < 2


def test_actor_critic_policy_and_value_shapes():
    model = ActorCritic(7, 2, 16)
    logits, values = model(torch.randn(4, 7))
    assert logits.shape == (4, 2)
    assert values.shape == (4,)
