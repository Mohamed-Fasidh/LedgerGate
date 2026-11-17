"""GPT-style decoder-only Transformer built from elementary PyTorch modules.

Shape convention throughout:
    B = batch, T = sequence length, C = embedding width,
    H = attention heads, D = C / H.
"""

import math
from typing import Optional

import torch
from torch import Tensor, nn
import torch.nn.functional as F

from .config import GPTConfig


class TokenEmbedding(nn.Module):
    """Learned lookup table: integer tokens [B,T] -> vectors [B,T,C]."""

    def __init__(self, vocab_size: int, embedding_dim: int) -> None:
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embedding_dim)

    def forward(self, token_ids: Tensor) -> Tensor:
        return self.embedding(token_ids)


class LearnedPositionalEmbedding(nn.Module):
    """Learned position table added to token vectors; positions are [0..T-1]."""

    def __init__(self, max_length: int, embedding_dim: int) -> None:
        super().__init__()
        self.embedding = nn.Embedding(max_length, embedding_dim)

    def forward(self, sequence_length: int, *, device: torch.device) -> Tensor:
        if sequence_length > self.embedding.num_embeddings:
            raise ValueError(
                f"sequence length {sequence_length} exceeds configured maximum "
                f"{self.embedding.num_embeddings}"
            )
        positions = torch.arange(sequence_length, device=device)
        return self.embedding(positions).unsqueeze(0)  # [1,T,C], broadcasts over B.


class LayerNorm(nn.Module):
    """Layer normalization over the final feature dimension, implemented directly."""

    def __init__(self, normalized_shape: int, eps: float = 1e-5) -> None:
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(normalized_shape))
        self.bias = nn.Parameter(torch.zeros(normalized_shape))

    def forward(self, x: Tensor) -> Tensor:
        mean = x.mean(dim=-1, keepdim=True)
        variance = (x - mean).pow(2).mean(dim=-1, keepdim=True)
        normalized = (x - mean) * torch.rsqrt(variance + self.eps)
        return normalized * self.weight + self.bias


class ScaledDotProductAttention(nn.Module):
    """Compute softmax(Q K^T / sqrt(D)) V with an optional boolean allow-mask.

    Q: [..., Tq, D], K: [..., Tk, D], V: [..., Tk, Dv]
    mask: broadcastable to [..., Tq, Tk], where True means attention is allowed.
    Returns output [..., Tq, Dv] and attention weights [..., Tq, Tk].
    """

    def forward(
        self,
        query: Tensor,
        key: Tensor,
        value: Tensor,
        mask: Optional[Tensor] = None,
    ) -> tuple[Tensor, Tensor]:
        if query.size(-1) != key.size(-1):
            raise ValueError("query and key feature dimensions must match")
        scores = torch.matmul(query, key.transpose(-2, -1)) / math.sqrt(query.size(-1))
        if mask is not None:
            if mask.dtype != torch.bool:
                raise TypeError("attention mask must be boolean (True means allowed)")
            scores = scores.masked_fill(~mask, torch.finfo(scores.dtype).min)
        weights = F.softmax(scores, dim=-1)
        output = torch.matmul(weights, value)
        return output, weights


class MultiHeadSelfAttention(nn.Module):
    """Project Q/K/V, split channels into heads, apply causal attention, join heads."""

    def __init__(self, embedding_dim: int, num_heads: int, dropout: float = 0.0) -> None:
        super().__init__()
        if embedding_dim % num_heads != 0:
            raise ValueError("embedding_dim must be divisible by num_heads")
        self.embedding_dim = embedding_dim
        self.num_heads = num_heads
        self.head_dim = embedding_dim // num_heads
        self.q_proj = nn.Linear(embedding_dim, embedding_dim)
        self.k_proj = nn.Linear(embedding_dim, embedding_dim)
        self.v_proj = nn.Linear(embedding_dim, embedding_dim)
        self.out_proj = nn.Linear(embedding_dim, embedding_dim)
        self.attention = ScaledDotProductAttention()
        self.attn_dropout = nn.Dropout(dropout)
        self.resid_dropout = nn.Dropout(dropout)

    def forward(self, x: Tensor, *, causal: bool = True) -> tuple[Tensor, Tensor]:
        batch_size, seq_len, channels = x.shape
        if channels != self.embedding_dim:
            raise ValueError(f"expected final dimension {self.embedding_dim}, got {channels}")

        def split_heads(tensor: Tensor) -> Tensor:
            return tensor.view(batch_size, seq_len, self.num_heads, self.head_dim).transpose(1, 2)

        query = split_heads(self.q_proj(x))  # [B,H,T,D]
        key = split_heads(self.k_proj(x))
        value = split_heads(self.v_proj(x))

        mask = None
        if causal:
            mask = torch.tril(
                torch.ones(seq_len, seq_len, device=x.device, dtype=torch.bool)
            )[None, None, :, :]  # [1,1,T,T], broadcasts over B and H.

        attended, weights = self.attention(query, key, value, mask)
        attended = self.attn_dropout(attended)
        attended = attended.transpose(1, 2).contiguous().view(batch_size, seq_len, channels)
        return self.resid_dropout(self.out_proj(attended)), weights


# Explicit name for readers who search for the causal variant.
CausalSelfAttention = MultiHeadSelfAttention


class FeedForward(nn.Module):
    """Position-wise MLP: C -> 4C -> C with GELU and dropout."""

    def __init__(self, embedding_dim: int, dropout: float = 0.0, expansion: int = 4) -> None:
        super().__init__()
        hidden_dim = expansion * embedding_dim
        self.network = nn.Sequential(
            nn.Linear(embedding_dim, hidden_dim),
            nn.GELU(),
            nn.Linear(hidden_dim, embedding_dim),
            nn.Dropout(dropout),
        )

    def forward(self, x: Tensor) -> Tensor:
        return self.network(x)


class GPTBlock(nn.Module):
    """Pre-layer-normalized decoder block with residual attention and MLP paths."""

    def __init__(self, config: GPTConfig) -> None:
        super().__init__()
        self.ln1 = LayerNorm(config.n_embd, config.layer_norm_eps)
        self.attention = MultiHeadSelfAttention(config.n_embd, config.n_head, config.dropout)
        self.ln2 = LayerNorm(config.n_embd, config.layer_norm_eps)
        self.feed_forward = FeedForward(config.n_embd, config.dropout)

    def forward(self, x: Tensor) -> Tensor:
        attention_output, _ = self.attention(self.ln1(x), causal=True)
        x = x + attention_output
        x = x + self.feed_forward(self.ln2(x))
        return x


class LanguageModelingHead(nn.Module):
    """Map hidden states [B,T,C] to vocabulary logits [B,T,V]."""

    def __init__(self, embedding_dim: int, vocab_size: int) -> None:
        super().__init__()
        self.projection = nn.Linear(embedding_dim, vocab_size, bias=False)

    def forward(self, hidden_states: Tensor) -> Tensor:
        return self.projection(hidden_states)


class GPTLanguageModel(nn.Module):
    """A compact GPT-style autoregressive language model."""

    def __init__(self, config: GPTConfig) -> None:
        super().__init__()
        self.config = config
        self.token_embedding = TokenEmbedding(config.vocab_size, config.n_embd)
        self.position_embedding = LearnedPositionalEmbedding(config.block_size, config.n_embd)
        self.embedding_dropout = nn.Dropout(config.dropout)
        self.blocks = nn.ModuleList([GPTBlock(config) for _ in range(config.n_layer)])
        self.final_norm = LayerNorm(config.n_embd, config.layer_norm_eps)
        self.lm_head = LanguageModelingHead(config.n_embd, config.vocab_size)

        if config.tie_weights:
            self.lm_head.projection.weight = self.token_embedding.embedding.weight

        self.apply(self._initialize_weights)

    @staticmethod
    def _initialize_weights(module: nn.Module) -> None:
        if isinstance(module, nn.Linear):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if module.bias is not None:
                nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def forward(
        self,
        input_ids: Tensor,
        targets: Optional[Tensor] = None,
    ) -> tuple[Tensor, Optional[Tensor]]:
        if input_ids.ndim != 2:
            raise ValueError("input_ids must have shape [B,T]")
        batch_size, seq_len = input_ids.shape
        if seq_len > self.config.block_size:
            raise ValueError(
                f"sequence length {seq_len} exceeds block_size {self.config.block_size}"
            )
        if targets is not None and targets.shape != input_ids.shape:
            raise ValueError("targets must have the same [B,T] shape as input_ids")

        token_vectors = self.token_embedding(input_ids)  # [B,T,C]
        position_vectors = self.position_embedding(seq_len, device=input_ids.device)  # [1,T,C]
        x = self.embedding_dropout(token_vectors + position_vectors)
        for block in self.blocks:
            x = block(x)
        hidden_states = self.final_norm(x)
        logits = self.lm_head(hidden_states)  # [B,T,V]

        loss = None
        if targets is not None:
            loss = F.cross_entropy(
                logits.reshape(batch_size * seq_len, self.config.vocab_size),
                targets.reshape(batch_size * seq_len),
            )
        return logits, loss

    @torch.inference_mode()
    def generate(
        self,
        input_ids: Tensor,
        max_new_tokens: int,
        *,
        temperature: float = 1.0,
        top_k: Optional[int] = None,
        eos_token_id: Optional[int] = None,
    ) -> Tensor:
        """Autoregressively append sampled tokens, cropping context to block_size."""
        if temperature <= 0:
            raise ValueError("temperature must be positive")
        if max_new_tokens < 0:
            raise ValueError("max_new_tokens cannot be negative")
        was_training = self.training
        self.eval()
        generated = input_ids
        try:
            for _ in range(max_new_tokens):
                context = generated[:, -self.config.block_size :]
                logits, _ = self(context)
                next_token_logits = logits[:, -1, :] / temperature
                if top_k is not None:
                    if top_k < 1:
                        raise ValueError("top_k must be at least 1")
                    k = min(top_k, next_token_logits.size(-1))
                    threshold = torch.topk(next_token_logits, k).values[:, -1, None]
                    next_token_logits = next_token_logits.masked_fill(
                        next_token_logits < threshold, float("-inf")
                    )
                probabilities = F.softmax(next_token_logits, dim=-1)
                next_token = torch.multinomial(probabilities, num_samples=1)
                generated = torch.cat((generated, next_token), dim=1)
                if eos_token_id is not None and torch.all(next_token == eos_token_id):
                    break
        finally:
            self.train(was_training)
        return generated
