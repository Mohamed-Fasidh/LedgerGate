import pytest
import torch

from transformer_from_scratch.config import GPTConfig
from transformer_from_scratch.model import (
    FeedForward,
    GPTBlock,
    GPTLanguageModel,
    LanguageModelingHead,
    LayerNorm,
    LearnedPositionalEmbedding,
    MultiHeadSelfAttention,
    ScaledDotProductAttention,
    TokenEmbedding,
)
from transformer_from_scratch.tokenizer import ByteTokenizer


def test_byte_tokenizer_round_trip_and_special_tokens():
    tokenizer = ByteTokenizer()
    text = "Hello, transformer! — café"
    ids = tokenizer.encode(text, add_bos=True, add_eos=True)
    assert ids[0] == tokenizer.bos_token_id
    assert ids[-1] == tokenizer.eos_token_id
    assert tokenizer.vocab_size == 258
    assert tokenizer.decode(ids) == text


def test_token_embedding_shape_and_gradient():
    layer = TokenEmbedding(vocab_size=20, embedding_dim=12)
    ids = torch.randint(0, 20, (2, 5))
    vectors = layer(ids)
    assert vectors.shape == (2, 5, 12)
    vectors.sum().backward()
    assert layer.embedding.weight.grad is not None


def test_learned_positional_embedding_shape_and_bounds():
    layer = LearnedPositionalEmbedding(max_length=8, embedding_dim=12)
    positions = layer(5, device=torch.device("cpu"))
    assert positions.shape == (1, 5, 12)
    with pytest.raises(ValueError, match="exceeds"):
        layer(9, device=torch.device("cpu"))


def test_scaled_dot_product_attention_matches_expected_weight_shape():
    attention = ScaledDotProductAttention()
    q = torch.randn(2, 3, 4, 8)
    k = torch.randn(2, 3, 4, 8)
    v = torch.randn(2, 3, 4, 6)
    mask = torch.tril(torch.ones(4, 4, dtype=torch.bool))[None, None]
    output, weights = attention(q, k, v, mask)
    assert output.shape == (2, 3, 4, 6)
    assert weights.shape == (2, 3, 4, 4)
    assert torch.allclose(weights.masked_select(~mask), torch.zeros_like(weights.masked_select(~mask)))
    assert torch.allclose(weights.sum(dim=-1), torch.ones(2, 3, 4), atol=1e-6)


def test_scaled_dot_product_attention_rejects_non_boolean_mask():
    attention = ScaledDotProductAttention()
    q = torch.randn(1, 2, 4)
    with pytest.raises(TypeError, match="boolean"):
        attention(q, q, q, torch.ones(2, 2))


def test_multi_head_attention_shapes_and_causal_weights():
    attention = MultiHeadSelfAttention(embedding_dim=16, num_heads=4)
    x = torch.randn(2, 6, 16)
    output, weights = attention(x)
    assert output.shape == (2, 6, 16)
    assert weights.shape == (2, 4, 6, 6)
    future = torch.triu(torch.ones(6, 6, dtype=torch.bool), diagonal=1)
    assert torch.all(weights.masked_select(future[None, None]) == 0)


def test_causal_mask_prevents_future_tokens_from_changing_earlier_outputs():
    torch.manual_seed(4)
    attention = MultiHeadSelfAttention(embedding_dim=16, num_heads=4).eval()
    original = torch.randn(1, 6, 16)
    changed = original.clone()
    changed[:, 4:, :] = torch.randn_like(changed[:, 4:, :]) * 20
    first, _ = attention(original)
    second, _ = attention(changed)
    assert torch.allclose(first[:, :4], second[:, :4], atol=1e-6)


def test_feed_forward_preserves_sequence_and_embedding_dimensions():
    layer = FeedForward(embedding_dim=16)
    assert layer(torch.randn(3, 7, 16)).shape == (3, 7, 16)


def test_layer_norm_normalizes_final_dimension():
    layer = LayerNorm(10)
    x = torch.randn(4, 3, 10) * 5 + 11
    y = layer(x)
    assert torch.allclose(y.mean(-1), torch.zeros(4, 3), atol=1e-5)
    assert torch.allclose(y.var(-1, unbiased=False), torch.ones(4, 3), atol=2e-4)


def test_decoder_block_residual_path_preserves_shape_and_gradient():
    config = GPTConfig(vocab_size=30, block_size=8, n_embd=16, n_head=4, n_layer=1)
    block = GPTBlock(config)
    x = torch.randn(2, 5, 16, requires_grad=True)
    y = block(x)
    assert y.shape == x.shape
    y.square().mean().backward()
    assert x.grad is not None


def test_language_model_head_maps_hidden_states_to_vocabulary_logits():
    head = LanguageModelingHead(embedding_dim=16, vocab_size=31)
    assert head(torch.randn(2, 5, 16)).shape == (2, 5, 31)


def test_gpt_forward_cross_entropy_and_shape_validation():
    config = GPTConfig(vocab_size=32, block_size=8, n_embd=16, n_head=4, n_layer=2)
    model = GPTLanguageModel(config)
    tokens = torch.randint(0, 32, (2, 8))
    logits, loss = model(tokens, tokens)
    assert logits.shape == (2, 8, 32)
    assert loss is not None and torch.isfinite(loss)
    with pytest.raises(ValueError, match="block_size"):
        model(torch.randint(0, 32, (1, 9)))


def test_full_model_is_causal_in_eval_mode():
    torch.manual_seed(9)
    config = GPTConfig(vocab_size=40, block_size=8, n_embd=16, n_head=4, n_layer=2, dropout=0.0)
    model = GPTLanguageModel(config).eval()
    first = torch.tensor([[1, 2, 3, 4, 5, 6]])
    second = first.clone()
    second[:, 4:] = torch.tensor([[17, 18]])
    logits_a, _ = model(first)
    logits_b, _ = model(second)
    assert torch.allclose(logits_a[:, :4], logits_b[:, :4], atol=1e-6)
