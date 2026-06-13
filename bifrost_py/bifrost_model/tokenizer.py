from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TinyIntTokenizer:
    """Whitespace tokenizer for explicit integer token IDs."""

    vocab_size: int
    tokenizer_name: str = "bifrost_integer_tokens"
    tokenizer_version: str = "phase4.v1"

    def __post_init__(self) -> None:
        if isinstance(self.vocab_size, bool) or self.vocab_size <= 0:
            raise ValueError("vocab_size must be a positive integer")

    def encode(self, text: str) -> list[int]:
        if not isinstance(text, str):
            raise TypeError("text must be a string")

        tokens: list[int] = []
        for raw_token in text.split():
            try:
                token_id = int(raw_token, 10)
            except ValueError as exc:
                raise ValueError(f"invalid integer token: {raw_token!r}") from exc
            self._validate_token_id(token_id)
            tokens.append(token_id)
        return tokens

    def decode(self, tokens: list[int]) -> str:
        return " ".join(str(self._validate_token_id(token_id)) for token_id in tokens)

    def to_config(self) -> dict[str, int | str]:
        return {
            "tokenizer_name": self.tokenizer_name,
            "tokenizer_version": self.tokenizer_version,
            "vocab_size": self.vocab_size,
        }

    def _validate_token_id(self, token_id: int) -> int:
        if isinstance(token_id, bool) or not isinstance(token_id, int):
            raise ValueError("token IDs must be integers")
        if token_id < 0:
            raise ValueError("token IDs must be non-negative")
        if token_id >= self.vocab_size:
            raise ValueError("token ID is outside vocab_size")
        return token_id
