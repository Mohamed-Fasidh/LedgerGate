"""Checkpoint serialization with model configuration and training metadata."""

from pathlib import Path
from typing import Any

import torch

from .config import GPTConfig
from .model import GPTLanguageModel


def save_checkpoint(
    path: str | Path,
    model: GPTLanguageModel,
    *,
    optimizer: torch.optim.Optimizer | None = None,
    history: dict[str, list[float]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "format_version": 1,
        "config": model.config.to_dict(),
        "model_state_dict": model.state_dict(),
        "history": history or {},
        "metadata": metadata or {},
    }
    if optimizer is not None:
        payload["optimizer_state_dict"] = optimizer.state_dict()
    torch.save(payload, path)


def load_checkpoint(
    path: str | Path,
    *,
    device: str | torch.device = "cpu",
) -> tuple[GPTLanguageModel, dict[str, Any]]:
    payload = torch.load(path, map_location=device, weights_only=True)
    if payload.get("format_version") != 1:
        raise ValueError("unsupported or missing checkpoint format_version")
    config = GPTConfig(**payload["config"])
    model = GPTLanguageModel(config).to(device)
    model.load_state_dict(payload["model_state_dict"])
    model.eval()
    return model, payload
