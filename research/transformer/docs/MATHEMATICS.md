# Transformer mathematics and tensor shapes

Let a batch contain token sequences of length (T), with token IDs in a vocabulary of size (V). The model width is (C), with (H) attention heads and per-head width (D=C/H).

## Embeddings

A token lookup maps an integer ID to a learned vector:

[
E_{tok}\in\mathbb{R}^{B\times T\times C}
]

A learned position table supplies one vector per position:

[
E_{pos}\in\mathbb{R}^{1\times T\times C},\qquad
X_0=Dropout(E_{tok}+E_{pos})
]

The singleton batch dimension in the positional embedding allows broadcasting across examples.

## Scaled dot-product attention

For one attention head:

[
Q=XW_Q,\quad K=XW_K,\quad V=XW_V
]

[
S=\frac{QK^\top}{\sqrt D}
]

The causal mask sets scores for future positions to a very negative value. Applying softmax over the key positions gives attention weights:

[
A=softmax(S+M),\qquad Z=AV
]

Here, (M_{ij}=0) when position (j\le i), and a large negative value when (j>i). Therefore a position cannot use information from tokens to its right.

With multiple heads, each head computes its own (Z_h). The results are concatenated and projected:

[
MultiHead(X)=Concat(Z_1,\ldots,Z_H)W_O
]

After splitting heads, Q/K/V have shape ([B,H,T,D]), while the score and weight tensors have shape ([B,H,T,T]).

## Decoder block

The implementation uses a pre-LayerNorm residual structure:

[
X'=X+MultiHead(LN(X))
]

[
Y=X'+FFN(LN(X'))
]

The feed-forward network operates independently at each sequence position:

[
FFN(x)=W_2\,GELU(W_1x+b_1)+b_2
]

Its hidden width is (4C). Residual paths provide a direct route for earlier representations; LayerNorm normalizes across the final feature dimension.

## Language-model objective

The language-model head produces vocabulary logits:

[
L\in\mathbb{R}^{B\times T\times V}
]

For each input position, cross-entropy compares the predicted distribution with the actual next token. Training minimizes the mean negative log-likelihood:

[
\mathcal L=-\frac{1}{BT}\sum_{b=1}^{B}\sum_{t=1}^{T}
\log p_\theta(x_{b,t+1}\mid x_{b,\le t})
]

The code receives shifted input/target windows, so the target at each location is the next token in the original stream.

## Generation

At inference time, the model takes the last at-most-(T_{max}) tokens, computes logits for the final position, divides by the positive temperature, optionally filters to the top (k) scores, applies softmax, and samples one token. That token is appended to the context and the process repeats until the token limit or EOS.

Temperature changes sampling sharpness; top-k changes the candidate set. Neither mechanism makes this small demonstration a reliable factual model.
