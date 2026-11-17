"""Dataset utilities for next-token language modeling."""

from pathlib import Path

import torch
from torch import Tensor
from torch.utils.data import TensorDataset

from .tokenizer import ByteTokenizer


def load_corpus(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")


def make_next_token_dataset(token_ids: list[int], block_size: int) -> TensorDataset:
    """Create disjoint fixed-length windows of (tokens[:-1], tokens[1:])."""
    width = block_size + 1
    windows = [token_ids[start : start + width] for start in range(0, len(token_ids) - width + 1, block_size)]
    if not windows:
        raise ValueError(
            f"corpus has {len(token_ids)} tokens; need at least block_size + 1 ({width})"
        )
    data = torch.tensor(windows, dtype=torch.long)
    return TensorDataset(data[:, :-1].contiguous(), data[:, 1:].contiguous())


def split_token_stream(text: str, tokenizer: ByteTokenizer, validation_fraction: float = 0.1):
    if not 0.0 < validation_fraction < 0.5:
        raise ValueError("validation_fraction must be in (0, 0.5)")
    tokens = tokenizer.encode(text, add_bos=True, add_eos=True)
    split_at = int(len(tokens) * (1.0 - validation_fraction))
    # The one-token overlap provides a valid next-token target at the boundary.
    train_tokens = tokens[:split_at]
    validation_tokens = tokens[split_at - 1 :]
    return train_tokens, validation_tokens
