from __future__ import annotations

from dataclasses import dataclass

import torch


@dataclass(frozen=True)
class TinyTransformerConfig:
    vocab_size: int = 128
    max_seq_len: int = 128
    num_layers: int = 2
    num_heads: int = 2
    num_kv_heads: int = 2
    head_dim: int = 8
    hidden_size: int = 16
    mlp_hidden_size: int = 64
    dtype: str = "float32"
    seed: int = 1234

    def __post_init__(self) -> None:
        positive_fields = {
            "vocab_size": self.vocab_size,
            "max_seq_len": self.max_seq_len,
            "num_layers": self.num_layers,
            "num_heads": self.num_heads,
            "num_kv_heads": self.num_kv_heads,
            "head_dim": self.head_dim,
            "hidden_size": self.hidden_size,
            "mlp_hidden_size": self.mlp_hidden_size,
        }
        for name, value in positive_fields.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")

        if self.num_heads != self.num_kv_heads:
            raise ValueError(
                "TinyTransformerConfig requires num_heads == num_kv_heads; "
                "grouped-query attention is out of scope for Phase 4"
            )
        if self.hidden_size != self.num_heads * self.head_dim:
            raise ValueError("hidden_size must equal num_heads * head_dim")
        if self.dtype != "float32":
            raise ValueError("Phase 4 required CPU tests support dtype='float32' only")

    @property
    def torch_dtype(self) -> torch.dtype:
        if self.dtype == "float32":
            return torch.float32
        raise ValueError(f"unsupported tiny transformer dtype: {self.dtype}")
