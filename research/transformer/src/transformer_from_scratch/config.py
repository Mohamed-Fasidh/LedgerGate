"""Small, serializable configuration for the educational GPT model."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class GPTConfig:
    vocab_size: int = 258
    block_size: int = 96
    n_embd: int = 64
    n_head: int = 4
    n_layer: int = 2
    dropout: float = 0.1
    layer_norm_eps: float = 1e-5
    tie_weights: bool = True

    def __post_init__(self) -> None:
        if self.vocab_size < 2:
            raise ValueError("vocab_size must be at least 2")
        if self.block_size < 1:
            raise ValueError("block_size must be positive")
        if self.n_embd < 1 or self.n_head < 1 or self.n_layer < 1:
            raise ValueError("n_embd, n_head, and n_layer must be positive")
        if self.n_embd % self.n_head != 0:
            raise ValueError("n_embd must be divisible by n_head")
        if not 0.0 <= self.dropout < 1.0:
            raise ValueError("dropout must be in [0, 1)")

    def to_dict(self) -> dict:
        return asdict(self)
