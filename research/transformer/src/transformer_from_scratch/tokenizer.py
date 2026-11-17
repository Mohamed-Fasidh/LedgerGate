"""A tiny, deterministic UTF-8 byte tokenizer (no downloaded vocabulary)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ByteTokenizer:
    """Map each UTF-8 byte to one token, plus explicit BOS and EOS tokens.

    Token IDs 0..255 are byte values; 256 is BOS and 257 is EOS. This tokenizer
    is intentionally simple and has a fixed vocabulary independent of corpus.
    It is useful for learning model mechanics, not for production NLP quality.
    """

    bos_token_id: int = 256
    eos_token_id: int = 257

    @property
    def vocab_size(self) -> int:
        return 258

    def encode(
        self,
        text: str,
        *,
        add_bos: bool = False,
        add_eos: bool = False,
    ) -> list[int]:
        tokens = list(text.encode("utf-8"))
        if add_bos:
            tokens.insert(0, self.bos_token_id)
        if add_eos:
            tokens.append(self.eos_token_id)
        return tokens

    def decode(self, token_ids: list[int], *, skip_special_tokens: bool = True) -> str:
        byte_values: list[int] = []
        for token_id in token_ids:
            token_id = int(token_id)
            if token_id in (self.bos_token_id, self.eos_token_id):
                if skip_special_tokens:
                    continue
                continue  # Special tokens have no byte representation.
            if not 0 <= token_id <= 255:
                raise ValueError(f"token ID {token_id} is outside this tokenizer's vocabulary")
            byte_values.append(token_id)
        return bytes(byte_values).decode("utf-8", errors="replace")
