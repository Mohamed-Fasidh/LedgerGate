# Transformer from Scratch

An educational GPT-style decoder-only Transformer implemented with visible PyTorch modules and tensor operations. This is an isolated research lab, not a replacement for LedgerGate's production model adapter and not a pretrained language model.

## Implemented

- UTF-8 byte tokenizer with 256 byte IDs plus BOS and EOS tokens.
- Token and learned positional embeddings.
- Scaled dot-product attention with an explicit boolean causal mask.
- Multi-head self-attention, pre-LayerNorm residual blocks, GELU feed-forward network.
- Vocabulary projection, next-token cross-entropy, and optional tied token/output weights.
- CPU training, optional CUDA automatic mixed precision, validation, best-checkpoint saving, and temperature/top-k generation.
- Unit tests for tensor shapes, gradients, attention masking, causality, training, checkpoint round trips, and generation.

## Install and test

From this directory:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
python -m pytest -q
```

## Train on CPU

```bash
python -m transformer_from_scratch.train --device cpu --epochs 30 --batch-size 16 --seed 1337
```

By default, the trainer reads `data/tiny_corpus.txt` and writes `outputs/best.pt`, `outputs/training_history.json`, and `outputs/loss_curve.svg`. The corpus is a small original teaching example. The model's outputs are not expected to be fluent or factually reliable.

Generate from the saved checkpoint:

```bash
python -m transformer_from_scratch.generate --checkpoint outputs/best.pt --prompt "The small machine" --max-new-tokens 100 --temperature 0.8 --top-k 30 --seed 1337
```

For optional CUDA training, install a PyTorch build appropriate for the local CUDA runtime, then use `--device auto --amp`. CPU-only execution makes no GPU performance claims.

## Tensor shapes

Let batch size be (B), sequence length (T), model width (C), heads (H), head width (D=C/H), and vocabulary size (V).

| Tensor | Shape |
|---|---|
| Token IDs | ([B,T]) |
| Token + position embeddings | ([B,T,C]) |
| Queries, keys, values after head split | ([B,H,T,D]) |
| Attention scores and weights | ([B,H,T,T]) |
| Decoder hidden states | ([B,T,C]) |
| Vocabulary logits | ([B,T,V]) |
| Next-token targets | ([B,T]) |

The attention and decoder equations are derived in [MATHEMATICS.md](docs/MATHEMATICS.md).

## Limits and research discipline

- Byte tokenization is transparent but less data-efficient than subword tokenization.
- The corpus is intentionally tiny and cannot support broad language capabilities.
- The implementation is educational and does not include FlashAttention, fused kernels, KV caching, distributed training, quantization, or production serving.
- Validation loss and sample generations are useful for this toy experiment only; they are not evidence of competitive model quality.
- Preserve the held-out validation split and record seed, hardware, framework version, and configuration when comparing changes.
- Never connect this research model to autonomous financial actions. LedgerGate's deterministic controls and human approvals remain authoritative.
