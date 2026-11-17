import pytest
import torch

from rl_research.algorithms.ppo import ppo_clipped_objective, train


def test_ppo_ratio_and_clipped_surrogate_are_finite():
    old = torch.tensor([0.0, 0.0])
    new = torch.tensor([0.0, 0.0])
    adv = torch.tensor([1.0, -1.0])
    loss, ratios = ppo_clipped_objective(new, old, adv, 0.2)
    assert torch.allclose(ratios, torch.ones(2))
    assert loss.item() == 0.0


def test_ppo_clips_positive_advantage_ratio():
    loss, ratios = ppo_clipped_objective(torch.log(torch.tensor([2.0])), torch.tensor([0.0]),
                                          torch.tensor([1.0]), 0.2)
    assert ratios.item() == 2.0
    assert loss.item() == pytest.approx(-1.2)


def test_short_ppo_training_produces_metrics_and_checkpoint(tmp_path):
    config = {"seed": 2, "updates": 2, "episodes_per_update": 2, "hidden_dim": 16,
              "learning_rate": 0.002, "gamma": 0.99, "gae_lambda": 0.95,
              "clip_epsilon": 0.2, "update_epochs": 2, "minibatch_size": 16,
              "value_coef": 0.5, "entropy_coef": 0.01, "eval_interval": 1,
              "eval_episodes": 3}
    result = train(config, tmp_path, "cpu")
    assert "success_rate" in result
    assert (tmp_path / "ppo" / "checkpoint.pt").exists()
    assert (tmp_path / "ppo" / "metrics.csv").exists()
