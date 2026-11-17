from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.distributions import Categorical

from rl_research.tracking import ExperimentTracker
from rl_research.utils import choose_device, set_seed

PREFIXES = ["Answer:", "Let's reason briefly:", "The key idea:"]
CONTENTS = [
    "plants use sunlight to make food",
    "photosynthesis converts light into stored chemical energy",
    "six times seven equals forty-two",
    "multiplying six by seven gives forty-two",
    "water freezes at zero degrees Celsius",
    "water boils at one hundred degrees Celsius",
    "the moon is made of cheese",
    "photosynthesis happens without light",
]
ENDINGS = ["That is the result.", "In summary.", "Hope this helps.", ""]

DEFAULT_PROMPTS = [
    {"id": "plants", "prompt": "Why do plants need sunlight?", "target_content_ids": [0, 1]},
    {"id": "multiply", "prompt": "What is 6 multiplied by 7?", "target_content_ids": [2, 3]},
    {"id": "freezing", "prompt": "At what temperature does water freeze?", "target_content_ids": [4]},
    {"id": "boiling", "prompt": "At what temperature does water boil?", "target_content_ids": [5]},
]


class TinyPromptResponsePolicy(nn.Module):
    """Prompt-conditioned response-slot policy, not a pretrained/autoregressive LLM."""

    def __init__(self, num_prompts: int, hidden_dim: int = 32):
        super().__init__()
        self.prompt_embedding = nn.Embedding(num_prompts, hidden_dim)
        self.trunk = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim), nn.Tanh(),
            nn.Linear(hidden_dim, hidden_dim), nn.Tanh(),
        )
        self.prefix_head = nn.Linear(hidden_dim, len(PREFIXES))
        self.content_head = nn.Linear(hidden_dim, len(CONTENTS))
        self.ending_head = nn.Linear(hidden_dim, len(ENDINGS))

    def forward(self, prompt_ids: torch.Tensor):
        hidden = self.trunk(self.prompt_embedding(prompt_ids.long()))
        return self.prefix_head(hidden), self.content_head(hidden), self.ending_head(hidden)

    def sample_actions(self, prompt_ids: torch.Tensor):
        distributions = [Categorical(logits=logits) for logits in self(prompt_ids)]
        actions = tuple(distribution.sample() for distribution in distributions)
        log_prob = sum(distribution.log_prob(action) for distribution, action in zip(distributions, actions))
        entropy = sum(distribution.entropy() for distribution in distributions)
        return actions, log_prob, entropy

    def evaluate_actions(self, prompt_ids: torch.Tensor, actions: tuple[torch.Tensor, ...]):
        distributions = [Categorical(logits=logits) for logits in self(prompt_ids)]
        log_prob = sum(distribution.log_prob(action) for distribution, action in zip(distributions, actions))
        entropy = sum(distribution.entropy() for distribution in distributions)
        return log_prob, entropy

    @torch.no_grad()
    def greedy_actions(self, prompt_ids: torch.Tensor):
        return tuple(logits.argmax(-1) for logits in self(prompt_ids))


def response_text(action_ids: tuple[int, int, int]) -> str:
    prefix_id, content_id, ending_id = action_ids
    return " ".join(part for part in (
        PREFIXES[prefix_id], CONTENTS[content_id], ENDINGS[ending_id]
    ) if part).strip()


def score_response(prompt: dict, action_ids: tuple[int, int, int]) -> tuple[float, float]:
    """Transparent synthetic rubric returning (reward, content correctness)."""
    prefix_id, content_id, ending_id = action_ids
    correct = float(content_id in prompt["target_content_ids"])
    reasoning_bonus = 0.12 if prefix_id in (1, 2) else 0.0
    concise_bonus = 0.05 if ending_id in (0, 1, 3) else 0.0
    return correct + reasoning_bonus + concise_bonus, correct


def normalize_group_rewards(rewards: torch.Tensor, eps: float = 1e-6) -> torch.Tensor:
    """Normalize rewards independently for every prompt group."""
    if rewards.ndim != 2 or rewards.shape[1] < 2:
        raise ValueError("rewards must have shape [num_prompts, group_size >= 2]")
    means = rewards.mean(dim=1, keepdim=True)
    stds = rewards.std(dim=1, keepdim=True, unbiased=False)
    return (rewards - means) / (stds + eps)


def grpo_clipped_objective(new_log_probs: torch.Tensor, old_log_probs: torch.Tensor,
                           advantages: torch.Tensor, clip_epsilon: float = 0.2):
    """Response-level clipped surrogate; production token-level GRPO is out of scope."""
    if clip_epsilon < 0:
        raise ValueError("clip_epsilon cannot be negative")
    ratios = torch.exp(new_log_probs - old_log_probs)
    unclipped = ratios * advantages
    clipped = torch.clamp(ratios, 1.0 - clip_epsilon, 1.0 + clip_epsilon) * advantages
    return -torch.minimum(unclipped, clipped).mean(), ratios


def _evaluate_greedy(policy: TinyPromptResponsePolicy, prompts: list[dict], device: torch.device):
    prompt_ids = torch.arange(len(prompts), device=device)
    actions = policy.greedy_actions(prompt_ids)
    responses, rewards, correct = [], [], []
    for index, prompt in enumerate(prompts):
        action = tuple(int(item[index].item()) for item in actions)
        reward, accuracy = score_response(prompt, action)
        responses.append({
            "prompt": prompt["prompt"], "response": response_text(action),
            "reward": reward, "content_correct": bool(accuracy),
        })
        rewards.append(reward)
        correct.append(accuracy)
    return {
        "eval_reward": float(np.mean(rewards)),
        "eval_content_accuracy": float(np.mean(correct)),
        "responses": responses,
    }


def train(config: dict, output_dir: str | Path, device_name: str = "auto",
          prompts: list[dict] | None = None) -> dict:
    set_seed(int(config["seed"]))
    device = choose_device(device_name)
    prompts = prompts or DEFAULT_PROMPTS
    if not prompts:
        raise ValueError("at least one prompt is required")
    if int(config["group_size"]) < 2:
        raise ValueError("group_size must be at least 2")
    for prompt in prompts:
        ids = prompt.get("target_content_ids", [])
        if not ids or any(not 0 <= int(item) < len(CONTENTS) for item in ids):
            raise ValueError(f"prompt {prompt.get('id')} must have valid target content IDs")

    policy = TinyPromptResponsePolicy(len(prompts), int(config["hidden_dim"])).to(device)
    optimizer = torch.optim.Adam(policy.parameters(), lr=float(config["learning_rate"]))
    tracker = ExperimentTracker(output_dir, "grpo", config)
    prompt_count, group_size = len(prompts), int(config["group_size"])
    prompt_ids = torch.arange(prompt_count, device=device).repeat_interleave(group_size)
    latest_candidates = []

    for iteration in range(1, int(config["iterations"]) + 1):
        # Generate one group of complete response candidates per prompt.
        with torch.no_grad():
            actions, old_logp, _ = policy.sample_actions(prompt_ids)
            flat_actions = tuple(action.detach() for action in actions)
            flat_rewards, flat_correct, latest_candidates = [], [], []
            for prompt_index, prompt in enumerate(prompts):
                candidates = []
                for candidate_index in range(group_size):
                    flat_index = prompt_index * group_size + candidate_index
                    action = tuple(int(item[flat_index].item()) for item in flat_actions)
                    reward, correct = score_response(prompt, action)
                    flat_rewards.append(reward)
                    flat_correct.append(correct)
                    candidates.append({
                        "response": response_text(action), "reward": reward,
                        "content_correct": bool(correct),
                    })
                latest_candidates.append({"prompt": prompt["prompt"], "candidates": candidates})
            rewards = torch.tensor(flat_rewards, dtype=torch.float32, device=device).reshape(prompt_count, group_size)
            advantages = normalize_group_rewards(rewards).reshape(-1).detach()
            old_logp = old_logp.detach()

        losses, clip_fractions, entropies = [], [], []
        for _ in range(int(config["update_epochs"])):
            new_logp, entropy = policy.evaluate_actions(prompt_ids, flat_actions)
            loss, ratios = grpo_clipped_objective(
                new_logp, old_logp, advantages, float(config["clip_epsilon"])
            )
            total_loss = loss - float(config["entropy_coef"]) * entropy.mean()
            optimizer.zero_grad()
            total_loss.backward()
            torch.nn.utils.clip_grad_norm_(policy.parameters(), 1.0)
            optimizer.step()
            losses.append(float(total_loss.item()))
            clip_fractions.append(float(((ratios - 1.0).abs() > config["clip_epsilon"]).float().mean().item()))
            entropies.append(float(entropy.mean().item()))

        greedy = _evaluate_greedy(policy, prompts, device)
        tracker.log(
            iteration=iteration,
            mean_group_reward=float(rewards.mean().item()),
            mean_candidate_correct=float(np.mean(flat_correct)),
            reward_std_within_groups=float(rewards.std(dim=1, unbiased=False).mean().item()),
            grpo_loss=float(np.mean(losses)),
            clip_fraction=float(np.mean(clip_fractions)),
            entropy=float(np.mean(entropies)),
            eval_reward=greedy["eval_reward"],
            eval_content_accuracy=greedy["eval_content_accuracy"],
        )

    summary = _evaluate_greedy(policy, prompts, device)
    checkpoint_dir = Path(tracker.directory)
    torch.save({
        "model": policy.state_dict(), "config": config, "prompts": prompts,
        "prefixes": PREFIXES, "contents": CONTENTS, "endings": ENDINGS,
        "algorithm": "educational_response_level_grpo",
    }, checkpoint_dir / "checkpoint.pt")
    (checkpoint_dir / "sample_candidates.json").write_text(
        json.dumps(latest_candidates, indent=2), encoding="utf-8"
    )
    (checkpoint_dir / "greedy_responses.json").write_text(
        json.dumps(summary["responses"], indent=2), encoding="utf-8"
    )
    tracker.close({key: value for key, value in summary.items() if key != "responses"})
    return {key: value for key, value in summary.items() if key != "responses"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the tiny educational response-level GRPO experiment")
    parser.add_argument("--config", default="configs/grpo.json")
    parser.add_argument("--output", default="outputs")
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()
    config_path = Path(args.config)
    if not config_path.exists():
        config_path = Path(__file__).resolve().parents[1] / "configs" / config_path.name
    config = json.loads(config_path.read_text(encoding="utf-8"))
    prompts_path = Path("data/grpo_prompts.json")
    if not prompts_path.exists():
        prompts_path = Path(__file__).resolve().parents[1] / "data" / "grpo_prompts.json"
    prompts = json.loads(prompts_path.read_text(encoding="utf-8"))
    print(json.dumps(train(config, args.output, args.device, prompts), indent=2))


if __name__ == "__main__":
    main()
