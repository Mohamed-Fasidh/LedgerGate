from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

import numpy as np
import torch

from rl_research.algorithms.actor_critic import train as train_actor_critic
from rl_research.algorithms.grpo import train as train_grpo
from rl_research.algorithms.policy_gradient import train as train_policy_gradient
from rl_research.algorithms.ppo import train as train_ppo


def _load_config(root: Path, name: str) -> dict:
    local = root / "configs" / f"{name}.json"
    packaged = Path(__file__).resolve().parent / "configs" / f"{name}.json"
    path = local if local.exists() else packaged
    if not path.exists():
        raise FileNotFoundError(f"configuration not found: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_grpo_prompts(root: Path) -> list[dict]:
    local = root / "data" / "grpo_prompts.json"
    packaged = Path(__file__).resolve().parent / "data" / "grpo_prompts.json"
    path = local if local.exists() else packaged
    if not path.exists():
        raise FileNotFoundError("GRPO prompt dataset not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _make_plots(output_dir: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed: skipped plots")
        return

    for filename, x_key, y_keys, algorithms in [
        ("lineworld_training_curves.png", "episode", ["episode_return", "eval_return"],
         ["policy_gradient", "actor_critic"]),
        ("ppo_training_curves.png", "update", ["rollout_return", "eval_return"], ["ppo"]),
    ]:
        fig, axis = plt.subplots(figsize=(8, 4.5))
        has_data = False
        for algorithm in algorithms:
            path = output_dir / algorithm / "metrics.json"
            if not path.exists():
                continue
            rows = json.loads(path.read_text(encoding="utf-8"))["history"]
            for y_key in y_keys:
                pairs = [(row[x_key], row[y_key]) for row in rows
                         if row.get(x_key) is not None and row.get(y_key) is not None]
                if pairs:
                    axis.plot([item[0] for item in pairs], [item[1] for item in pairs],
                              label=f"{algorithm}: {y_key}")
                    has_data = True
        if has_data:
            axis.set_xlabel(x_key.replace("_", " ").title())
            axis.set_ylabel("Return")
            axis.set_title(filename.replace("_", " ").replace(".png", "").title())
            axis.grid(alpha=0.25)
            axis.legend(fontsize=8)
            fig.tight_layout()
            fig.savefig(output_dir / filename, dpi=140)
        plt.close(fig)

    grpo_path = output_dir / "grpo" / "metrics.json"
    if grpo_path.exists():
        rows = json.loads(grpo_path.read_text(encoding="utf-8"))["history"]
        fig, axis = plt.subplots(figsize=(8, 4.5))
        iterations = [row["iteration"] for row in rows]
        axis.plot(iterations, [row["mean_group_reward"] for row in rows], label="sampled group reward")
        axis.plot(iterations, [row["eval_reward"] for row in rows], label="greedy evaluation reward")
        axis.set_xlabel("Iteration")
        axis.set_ylabel("Reward")
        axis2 = axis.twinx()
        axis2.plot(iterations, [row["eval_content_accuracy"] for row in rows],
                   linestyle="--", label="greedy content accuracy")
        axis2.set_ylabel("Content accuracy")
        lines = axis.get_lines() + axis2.get_lines()
        axis.legend(lines, [line.get_label() for line in lines], fontsize=8)
        axis.grid(alpha=0.25)
        fig.tight_layout()
        fig.savefig(output_dir / "grpo_training_curves.png", dpi=140)
        plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run all compact RL research experiments")
    parser.add_argument("--only", choices=["all", "policy_gradient", "actor_critic", "ppo", "grpo"],
                        default="all")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--output", default="outputs")
    parser.add_argument("--threads", type=int, default=1)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error("--threads must be at least 1")
    torch.set_num_threads(args.threads)
    root = Path(__file__).resolve().parents[2]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    metadata = {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "torch_version": torch.__version__,
        "numpy_version": np.__version__,
        "requested_device": args.device,
        "cuda_available": bool(torch.cuda.is_available()),
        "cuda_device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "torch_threads": torch.get_num_threads(),
    }
    (output / "run_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    jobs = {
        "policy_gradient": lambda: train_policy_gradient(
            _load_config(root, "policy_gradient"), output, args.device
        ),
        "actor_critic": lambda: train_actor_critic(
            _load_config(root, "actor_critic"), output, args.device
        ),
        "ppo": lambda: train_ppo(_load_config(root, "ppo"), output, args.device),
        "grpo": lambda: train_grpo(
            _load_config(root, "grpo"), output, args.device, _load_grpo_prompts(root)
        ),
    }
    selected = jobs if args.only == "all" else {args.only: jobs[args.only]}
    results = {}
    for name, job in selected.items():
        print(f"Running {name} on {args.device} ...", flush=True)
        results[name] = job()
        print(f"{name}: {results[name]}", flush=True)
    if args.only == "all":
        (output / "experiment_summary.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
        _make_plots(output)
        print(f"Wrote experiment outputs to {output.resolve()}")


if __name__ == "__main__":
    main()
