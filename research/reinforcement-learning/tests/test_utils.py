import pytest
import torch

from rl_research.utils import compute_gae, discounted_returns, normalize_advantages


def test_discounted_returns_resets_at_done_boundaries():
    got = discounted_returns([1.0, 1.0, 2.0], gamma=0.5, dones=[0, 1, 1])
    assert torch.allclose(got, torch.tensor([1.5, 1.0, 2.0]))


def test_gae_matches_hand_computation_and_bootstraps_masked():
    rewards = torch.tensor([1.0, 1.0])
    values = torch.tensor([0.5, 0.4])
    dones = torch.tensor([0.0, 1.0])
    adv, targets = compute_gae(rewards, values, dones, 0.0, gamma=0.9, gae_lambda=0.8)
    assert torch.allclose(adv, torch.tensor([1.292, 0.6]), atol=1e-5)
    assert torch.allclose(targets, torch.tensor([1.792, 1.0]), atol=1e-5)


def test_gae_uses_bootstrap_when_episode_is_not_terminal():
    adv, _ = compute_gae(torch.tensor([1.0]), torch.tensor([0.5]), torch.tensor([0.0]),
                         bootstrap_value=2.0, gamma=0.9, gae_lambda=0.95)
    assert adv.item() == pytest.approx(2.3)


def test_advantage_normalization_has_zero_mean_and_unit_population_std():
    normalized = normalize_advantages(torch.tensor([1.0, 2.0, 3.0]))
    assert normalized.mean().item() == pytest.approx(0.0, abs=1e-6)
    assert normalized.std(unbiased=False).item() == pytest.approx(1.0, abs=1e-6)


def test_empty_advantage_normalization_rejected():
    with pytest.raises(ValueError):
        normalize_advantages(torch.tensor([]))
