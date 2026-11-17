"""Reproducible CPU/CUDA training and validation loops."""

from contextlib import nullcontext
import random

import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from .checkpoint import save_checkpoint
from .model import GPTLanguageModel


def seed_everything(seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    if hasattr(torch.backends, "cudnn"):
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False


def choose_device(requested: str = "auto") -> torch.device:
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    device = torch.device(requested)
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but this PyTorch runtime has no CUDA support")
    return device


@torch.no_grad()
def evaluate(model: GPTLanguageModel, loader: DataLoader, device: torch.device) -> float:
    model.eval()
    loss_sum = 0.0
    example_count = 0
    for input_ids, targets in loader:
        input_ids, targets = input_ids.to(device), targets.to(device)
        _, loss = model(input_ids, targets)
        assert loss is not None
        count = input_ids.size(0)
        loss_sum += float(loss.item()) * count
        example_count += count
    if example_count == 0:
        raise ValueError("validation dataset is empty")
    return loss_sum / example_count


def train_model(
    model: GPTLanguageModel,
    train_dataset: TensorDataset,
    validation_dataset: TensorDataset,
    *,
    epochs: int = 30,
    batch_size: int = 16,
    learning_rate: float = 3e-4,
    seed: int = 1337,
    device: str | torch.device = "cpu",
    checkpoint_path: str | None = None,
    amp: bool = False,
) -> dict[str, list[float]]:
    if epochs < 1 or batch_size < 1:
        raise ValueError("epochs and batch_size must be positive")
    device = torch.device(device)
    model.to(device)
    generator = torch.Generator().manual_seed(seed)
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, generator=generator)
    validation_loader = DataLoader(validation_dataset, batch_size=batch_size, shuffle=False)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=0.01)
    use_amp = bool(amp and device.type == "cuda")
    use_bf16 = bool(use_amp and torch.cuda.is_bf16_supported())
    amp_dtype = torch.bfloat16 if use_bf16 else torch.float16
    scaler_enabled = bool(use_amp and not use_bf16)
    try:
        scaler = torch.amp.GradScaler("cuda", enabled=scaler_enabled)
    except (AttributeError, TypeError):  # Compatibility with older supported PyTorch releases.
        scaler = torch.cuda.amp.GradScaler(enabled=scaler_enabled)

    history: dict[str, list[float]] = {"train_loss": [], "validation_loss": []}
    best_validation = float("inf")

    for _epoch in range(epochs):
        model.train()
        epoch_loss_sum = 0.0
        epoch_example_count = 0
        for input_ids, targets in train_loader:
            input_ids, targets = input_ids.to(device), targets.to(device)
            optimizer.zero_grad(set_to_none=True)
            context = (
                torch.autocast(device_type="cuda", dtype=amp_dtype)
                if use_amp else nullcontext()
            )
            with context:
                _, loss = model(input_ids, targets)
            assert loss is not None
            if scaler.is_enabled():
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                scaler.step(optimizer)
                scaler.update()
            else:
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()
            count = input_ids.size(0)
            epoch_loss_sum += float(loss.detach().item()) * count
            epoch_example_count += count

        train_loss = epoch_loss_sum / epoch_example_count
        validation_loss = evaluate(model, validation_loader, device)
        history["train_loss"].append(train_loss)
        history["validation_loss"].append(validation_loss)

        if checkpoint_path and validation_loss < best_validation:
            best_validation = validation_loss
            save_checkpoint(
                checkpoint_path,
                model,
                optimizer=optimizer,
                history=history,
                metadata={"epoch": _epoch + 1, "best_validation_loss": best_validation},
            )

    if checkpoint_path and best_validation == float("inf"):
        save_checkpoint(checkpoint_path, model, optimizer=optimizer, history=history)
    model.eval()
    return history
