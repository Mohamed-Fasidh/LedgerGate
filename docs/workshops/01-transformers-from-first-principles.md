# Transformers from First Principles

**Duration:** 18 minutes

## Learning objectives
Explain token and positional embeddings, scaled dot-product attention, causal masking, multi-head attention, residual connections, decoder blocks, and next-token loss.

## Prerequisites
Python, PyTorch tensors, matrix multiplication, softmax, and basic gradient descent.

## Mathematics and whiteboard
For hidden states X with shape [B,T,C], form Q=XWq, K=XWk, V=XWv. Attention is softmax(QKᵀ / sqrt(dk) + M)V. The scale controls dot-product magnitude. The causal mask M prevents position t from attending to positions greater than t. With H heads, each head has dimension C/H; head outputs are concatenated and projected. A decoder block adds attention and feed-forward outputs through residual paths, with normalization. The language-model head maps [B,T,C] to [B,T,V] logits. Cross-entropy trains position t to predict token t+1.

## Timed teaching script
- 0–2 min: motivate autoregressive next-token prediction; identify B, T, C, V.
- 2–5 min: derive Q, K, V and scaled dot-product attention.
- 5–8 min: draw a lower-triangular causal mask and explain future-token leakage.
- 8–11 min: explain multiple heads, residual connections, normalization, and MLP.
- 11–15 min: inspect the repository's transformer model and trace tensor shapes.
- 15–18 min: ask students to diagnose an incorrect mask and predict output shapes.

## Coding demonstration
From research/transformer, install with pip install -e '.[test]' and run python -m pytest -q. Inspect the model forward pass: token IDs [B,T], embeddings [B,T,C], attention weights [B,H,T,T], logits [B,T,V]. Run the CPU training script and generation command described in that package README; report the actual loss and generated text from that run.

## Common mistakes
- Omitting the square-root scaling.
- Reversing the mask convention; this implementation uses True to mean allowed.
- Confusing causal masking with padding masking.
- Predicting the current token instead of the next token.
- Claiming a tiny corpus experiment demonstrates general language understanding.

## Applications
Autoregressive language models, code completion, sequence modelling, and generative decoder architectures.

## Interview questions
1. Why divide attention scores by sqrt(dk)?
2. What does a causal mask prevent?
3. Why use multiple heads?
4. What are the shapes of attention weights and logits?
5. How would you test that a future token cannot affect earlier-position outputs?

## Student exercise
For B=2, T=8, C=64, H=4, calculate head dimension and attention-weight shape. Add a test that strictly upper-triangular attention weights are zero within tolerance.

## Instructor note
This is a small educational decoder trained from scratch on a tiny corpus, not a pretrained foundation model.
