import pytest
import torch

from rl_research.algorithms.grpo import (
    DEFAULT_PROMPTS, TinyPromptResponsePolicy, grpo_clipped_objective,
    normalize_group_rewards, response_text, score_response, train,
)


def test_group_reward_normalization_is_per_prompt():
    rewards = torch.tensor([[1.0, 2.0, 3.0], [10.0, 10.0, 10.0]])
    advantages = normalize_group_rewards(rewards)
    assert torch.allclose(advantages[0].mean(), torch.tensor(0.0), atol=1e-6)
    assert torch.allclose(advantages[1], torch.zeros(3))
    assert torch.allclose(advantages[0].std(unbiased=False), torch.tensor(1.0), atol=1e-5)


def test_grpo_candidate_generation_and_prompt_conditioned_shapes():
    policy = TinyPromptResponsePolicy(num_prompts=4, hidden_dim=16)
    prompt_ids = torch.tensor([0, 0, 1, 1])
    actions, logp, entropy = policy.sample_actions(prompt_ids)
    assert [action.shape for action in actions] == [(4,), (4,), (4,)]
    assert logp.shape == entropy.shape == (4,)
    generated = response_text(tuple(int(action[0]) for action in actions))
    assert isinstance(generated, str) and generated.strip()


def test_reward_rubric_distinguishes_correct_content():
    reward_ok, correct = score_response(DEFAULT_PROMPTS[1], (1, 2, 1))
    reward_bad, incorrect = score_response(DEFAULT_PROMPTS[1], (1, 6, 1))
    assert correct == 1.0 and incorrect == 0.0
    assert reward_ok > reward_bad


def test_grpo_clipped_objective_is_differentiable():
    new = torch.tensor([0.0, 0.1], requires_grad=True)
    old = torch.zeros(2)
    advantages = torch.tensor([1.0, -1.0])
    loss, ratios = grpo_clipped_objective(new, old, advantages)
    loss.backward()
    assert torch.isfinite(loss) and torch.isfinite(ratios).all()
    assert new.grad is not None


def test_short_grpo_training_saves_samples_and_checkpoint(tmp_path):
    config = {"seed": 7, "iterations": 3, "group_size": 4, "hidden_dim": 16,
              "learning_rate": 0.02, "clip_epsilon": 0.2, "update_epochs": 2,
              "entropy_coef": 0.01}
    result = train(config, tmp_path, "cpu")
    assert 0.0 <= result["eval_content_accuracy"] <= 1.0
    assert (tmp_path / "grpo" / "checkpoint.pt").exists()
    assert (tmp_path / "grpo" / "sample_candidates.json").exists()
