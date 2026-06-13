from __future__ import annotations

import math
from typing import TypeAlias

import torch
from torch import nn
from torch.nn import functional as F

from bifrost_model.config import TinyTransformerConfig
from bifrost_model.determinism import set_deterministic

LayerKV: TypeAlias = tuple[torch.Tensor, torch.Tensor]
PastKeyValues: TypeAlias = list[LayerKV]


class TinySelfAttention(nn.Module):
    def __init__(self, config: TinyTransformerConfig) -> None:
        super().__init__()
        self.config = config
        self.q_proj = nn.Linear(config.hidden_size, config.hidden_size, bias=False)
        self.k_proj = nn.Linear(config.hidden_size, config.hidden_size, bias=False)
        self.v_proj = nn.Linear(config.hidden_size, config.hidden_size, bias=False)
        self.out_proj = nn.Linear(config.hidden_size, config.hidden_size, bias=False)

    def forward(
        self,
        x: torch.Tensor,
        past_key_value: LayerKV | None,
        use_cache: bool,
    ) -> tuple[torch.Tensor, LayerKV | None]:
        seq_len = x.shape[0]
        config = self.config

        q = self.q_proj(x).view(seq_len, config.num_heads, config.head_dim)
        k = self.k_proj(x).view(seq_len, config.num_kv_heads, config.head_dim)
        v = self.v_proj(x).view(seq_len, config.num_kv_heads, config.head_dim)

        if past_key_value is None:
            past_len = 0
            all_k = k
            all_v = v
        else:
            past_k, past_v = past_key_value
            past_len = past_k.shape[0]
            all_k = torch.cat((past_k, k), dim=0)
            all_v = torch.cat((past_v, v), dim=0)

        scores = torch.einsum("thd,shd->hts", q, all_k)
        scores = scores / math.sqrt(config.head_dim)

        query_positions = torch.arange(
            past_len, past_len + seq_len, device=x.device
        ).unsqueeze(1)
        key_positions = torch.arange(all_k.shape[0], device=x.device).unsqueeze(0)
        causal_mask = key_positions <= query_positions
        scores = scores.masked_fill(~causal_mask.unsqueeze(0), float("-inf"))

        weights = F.softmax(scores, dim=-1)
        attn_output = torch.einsum("hts,shd->thd", weights, all_v)
        attn_output = attn_output.reshape(seq_len, config.hidden_size)

        updated: LayerKV | None = None
        if use_cache:
            updated = (all_k.contiguous(), all_v.contiguous())
        return self.out_proj(attn_output), updated


class TinyTransformerBlock(nn.Module):
    def __init__(self, config: TinyTransformerConfig) -> None:
        super().__init__()
        self.ln_1 = nn.LayerNorm(config.hidden_size)
        self.attn = TinySelfAttention(config)
        self.ln_2 = nn.LayerNorm(config.hidden_size)
        self.mlp = nn.Sequential(
            nn.Linear(config.hidden_size, config.mlp_hidden_size),
            nn.GELU(),
            nn.Linear(config.mlp_hidden_size, config.hidden_size),
        )

    def forward(
        self,
        x: torch.Tensor,
        past_key_value: LayerKV | None,
        use_cache: bool,
    ) -> tuple[torch.Tensor, LayerKV | None]:
        attn_output, updated = self.attn(self.ln_1(x), past_key_value, use_cache)
        x = x + attn_output
        x = x + self.mlp(self.ln_2(x))
        return x, updated


class TinyTransformer(nn.Module):
    """CPU-only GPT-style model used for Phase 4 KV correctness tests."""

    def __init__(self, config: TinyTransformerConfig | None = None) -> None:
        super().__init__()
        self.config = config or TinyTransformerConfig()
        set_deterministic(self.config.seed)

        self.token_embeddings = nn.Embedding(
            self.config.vocab_size,
            self.config.hidden_size,
            dtype=self.config.torch_dtype,
        )
        self.position_embeddings = nn.Embedding(
            self.config.max_seq_len,
            self.config.hidden_size,
            dtype=self.config.torch_dtype,
        )
        self.layers = nn.ModuleList(
            TinyTransformerBlock(self.config) for _ in range(self.config.num_layers)
        )
        self.final_ln = nn.LayerNorm(self.config.hidden_size)
        self.lm_head = nn.Linear(
            self.config.hidden_size, self.config.vocab_size, bias=False
        )
        self._reset_parameters()

    def _reset_parameters(self) -> None:
        generator = torch.Generator(device="cpu")
        generator.manual_seed(self.config.seed)

        for module in self.modules():
            if isinstance(module, nn.Embedding):
                nn.init.normal_(module.weight, mean=0.0, std=0.02, generator=generator)
            elif isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight, gain=1.0, generator=generator)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
            elif isinstance(module, nn.LayerNorm):
                nn.init.ones_(module.weight)
                nn.init.zeros_(module.bias)

    def forward(
        self,
        input_ids: torch.Tensor,
        past_key_values: PastKeyValues | None = None,
        use_cache: bool = True,
    ) -> tuple[torch.Tensor, PastKeyValues | None]:
        input_ids = self._normalize_input_ids(input_ids)
        seq_len = input_ids.shape[0]

        past_len = self._past_length(past_key_values)
        if past_len + seq_len > self.config.max_seq_len:
            raise ValueError("input plus past_key_values exceeds max_seq_len")

        self._validate_past_key_values(past_key_values, past_len)

        positions = torch.arange(
            past_len,
            past_len + seq_len,
            dtype=torch.long,
            device=input_ids.device,
        )
        x = self.token_embeddings(input_ids) + self.position_embeddings(positions)

        updated_cache: PastKeyValues = []
        for layer_index, layer in enumerate(self.layers):
            layer_past = None if past_key_values is None else past_key_values[layer_index]
            x, updated = layer(x, layer_past, use_cache)
            if use_cache:
                if updated is None:
                    raise RuntimeError("cache update missing from tiny transformer layer")
                updated_cache.append(updated)

        logits = self.lm_head(self.final_ln(x))
        return logits, updated_cache if use_cache else None

    def prefill(self, input_ids: torch.Tensor) -> tuple[torch.Tensor, PastKeyValues]:
        logits, past_key_values = self.forward(input_ids, use_cache=True)
        if past_key_values is None:
            raise RuntimeError("prefill expected past_key_values")
        return logits, past_key_values

    def decode_one(
        self,
        input_id: int | torch.Tensor,
        past_key_values: PastKeyValues,
    ) -> tuple[torch.Tensor, PastKeyValues]:
        input_ids = torch.as_tensor([input_id], dtype=torch.long)
        logits, updated = self.forward(input_ids, past_key_values, use_cache=True)
        if updated is None:
            raise RuntimeError("decode_one expected past_key_values")
        return logits[0], updated

    @torch.no_grad()
    def generate_greedy(self, input_ids: torch.Tensor, max_new_tokens: int) -> torch.Tensor:
        if max_new_tokens < 0:
            raise ValueError("max_new_tokens must be non-negative")

        input_ids = self._normalize_input_ids(input_ids)
        if max_new_tokens == 0:
            return input_ids.clone()

        logits, past_key_values = self.prefill(input_ids)
        generated = input_ids.tolist()
        next_id = int(torch.argmax(logits[-1]).item())

        for token_index in range(max_new_tokens):
            generated.append(next_id)
            if token_index == max_new_tokens - 1:
                break
            next_logits, past_key_values = self.decode_one(next_id, past_key_values)
            next_id = int(torch.argmax(next_logits).item())

        return torch.tensor(generated, dtype=torch.long)

    def _normalize_input_ids(self, input_ids: torch.Tensor) -> torch.Tensor:
        if not torch.is_tensor(input_ids):
            input_ids = torch.as_tensor(input_ids, dtype=torch.long)
        if input_ids.dim() == 2 and input_ids.shape[0] == 1:
            input_ids = input_ids[0]
        if input_ids.dim() != 1:
            raise ValueError("TinyTransformer supports 1-D input_ids or batch size 1")
        if input_ids.numel() == 0:
            raise ValueError("input_ids must contain at least one token")
        input_ids = input_ids.to(dtype=torch.long, device=torch.device("cpu"))
        if torch.any(input_ids < 0) or torch.any(input_ids >= self.config.vocab_size):
            raise ValueError("input_ids contain token outside vocab_size")
        return input_ids

    def _past_length(self, past_key_values: PastKeyValues | None) -> int:
        if past_key_values is None:
            return 0
        if len(past_key_values) != self.config.num_layers:
            raise ValueError("past_key_values must contain one KV tuple per layer")
        return past_key_values[0][0].shape[0]

    def _validate_past_key_values(
        self,
        past_key_values: PastKeyValues | None,
        expected_seq_len: int,
    ) -> None:
        if past_key_values is None:
            return

        expected_shape = (
            expected_seq_len,
            self.config.num_kv_heads,
            self.config.head_dim,
        )
        for layer_index, layer_kv in enumerate(past_key_values):
            if len(layer_kv) != 2:
                raise ValueError(f"layer {layer_index} cache must be a key/value tuple")
            key, value = layer_kv
            if tuple(key.shape) != expected_shape:
                raise ValueError(f"layer {layer_index} key has wrong shape")
            if tuple(value.shape) != expected_shape:
                raise ValueError(f"layer {layer_index} value has wrong shape")
            if key.dtype != self.config.torch_dtype or value.dtype != self.config.torch_dtype:
                raise ValueError(f"layer {layer_index} cache has wrong dtype")
