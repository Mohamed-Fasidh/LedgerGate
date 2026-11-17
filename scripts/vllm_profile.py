#!/usr/bin/env python3
"""Print a vLLM launch command from an experiment profile; does not launch it."""
import argparse
import json
import shlex
from pathlib import Path


def build_command(profile: dict, model: str, host: str, port: int) -> list[str]:
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if not model.strip():
        raise ValueError("model must not be empty")
    command = [
        "vllm", "serve", model, "--host", host, "--port", str(port),
        "--dtype", profile["dtype"],
        "--gpu-memory-utilization", str(profile["gpu_memory_utilization"]),
        "--max-num-seqs", str(profile["max_num_seqs"]),
        "--max-num-batched-tokens", str(profile["max_num_batched_tokens"]),
        "--max-model-len", str(profile["max_model_len"]),
        "--kv-cache-dtype", profile.get("kv_cache_dtype", "auto"),
    ]
    if profile.get("quantization"):
        command += ["--quantization", profile["quantization"]]
    if profile.get("load_format"):
        command += ["--load-format", profile["load_format"]]
    return command


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", help="Profile name from experiments.json")
    parser.add_argument("--model", required=True, help="Model ID or local checkpoint path")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--matrix", default=str(Path(__file__).resolve().parents[1] / "experiments.json"))
    args = parser.parse_args()
    matrix = json.loads(Path(args.matrix).read_text(encoding="utf-8"))
    profiles = {item["name"]: item for item in matrix["profiles"]}
    if args.profile not in profiles:
        parser.error(f"unknown profile {args.profile!r}; choose from {', '.join(profiles)}")
    print(shlex.join(build_command(profiles[args.profile], args.model, args.host, args.port)))
    print("# Review the command against the installed vLLM --help and hardware/checkpoint compatibility.")


if __name__ == "__main__":
    main()
