"""CI-safe fake vLLM interfaces and CPU KV buffers for Phase 7 tests.

Fake KV cache tensors use this documented layout:

```
layer_name -> tensor[num_blocks, block_size, 2, num_heads, head_dim]
```

The dimension of size 2 represents vLLM-owned key/value state opaquely. These
helpers do not reinterpret tensor semantics; they only provide deterministic
CPU tensors that tests can stage, clone, zero, compare, and corrupt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping

import torch

FakeKVCaches = dict[str, torch.Tensor]


class FakeConnectorRole(str, Enum):
    kv_producer = "kv_producer"
    kv_consumer = "kv_consumer"
    kv_both = "kv_both"

    @classmethod
    def coerce(cls, value: "FakeConnectorRole | str") -> "FakeConnectorRole":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except ValueError as exc:
            raise ValueError(f"unsupported fake connector role: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class FakeKVTransferConfig:
    kv_connector: str = "BifrostVllmKVConnector"
    kv_connector_module_path: str = "vllm_bifrost"
    kv_connector_extra_config: Mapping[str, Any] = field(default_factory=dict)
    kv_role: FakeConnectorRole | str = FakeConnectorRole.kv_both
    kv_rank: int = 0
    kv_parallel_size: int = 1
    kv_ip: str | None = None
    kv_port: int | None = None
    kv_load_failure_policy: str = "recompute"
    engine_id: str = "fake-vllm-engine"
    kv_buffer_device: str = "cpu"
    kv_buffer_size: int | None = None
    is_kv_transfer_instance: bool = True

    @property
    def role(self) -> FakeConnectorRole:
        return FakeConnectorRole.coerce(self.kv_role)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kv_connector": self.kv_connector,
            "kv_connector_module_path": self.kv_connector_module_path,
            "kv_connector_extra_config": dict(self.kv_connector_extra_config),
            "kv_role": self.role.value,
            "kv_rank": self.kv_rank,
            "kv_parallel_size": self.kv_parallel_size,
            "kv_ip": self.kv_ip,
            "kv_port": self.kv_port,
            "kv_load_failure_policy": self.kv_load_failure_policy,
            "engine_id": self.engine_id,
            "kv_buffer_device": self.kv_buffer_device,
            "kv_buffer_size": self.kv_buffer_size,
            "is_kv_transfer_instance": self.is_kv_transfer_instance,
        }


@dataclass(frozen=True, slots=True)
class FakeKVCacheConfig:
    num_layers: int = 2
    num_blocks: int = 4
    block_size: int = 8
    num_heads: int = 2
    head_dim: int = 4
    dtype: torch.dtype = torch.float32
    layer_names: tuple[str, ...] | None = None
    layout_fingerprint: str = "fake-vllm-layout-v1"

    def __post_init__(self) -> None:
        _require_positive_int("num_layers", self.num_layers)
        _require_positive_int("num_blocks", self.num_blocks)
        _require_positive_int("block_size", self.block_size)
        _require_positive_int("num_heads", self.num_heads)
        _require_positive_int("head_dim", self.head_dim)
        if not isinstance(self.dtype, torch.dtype):
            raise TypeError("dtype must be a torch.dtype")
        if self.layer_names is None:
            names = tuple(f"layer_{layer_id}" for layer_id in range(self.num_layers))
        else:
            names = tuple(str(name) for name in self.layer_names)
            if len(names) != self.num_layers:
                raise ValueError("layer_names length must equal num_layers")
        object.__setattr__(self, "layer_names", names)

    @property
    def layer_shape(self) -> tuple[int, int, int, int, int]:
        return (
            self.num_blocks,
            self.block_size,
            2,
            self.num_heads,
            self.head_dim,
        )


@dataclass(frozen=True, slots=True)
class FakeVllmConfig:
    kv_transfer_config: FakeKVTransferConfig = field(
        default_factory=FakeKVTransferConfig
    )
    kv_cache_config: FakeKVCacheConfig = field(default_factory=FakeKVCacheConfig)
    model_config: Mapping[str, Any] = field(
        default_factory=lambda: {"model": "fake-vllm-model"}
    )
    parallel_config: Mapping[str, Any] = field(default_factory=dict)
    scheduler_config: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FakeRequest:
    request_id: str = "request-0"
    prompt_token_ids: tuple[int, ...] = ()
    prefix_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "prompt_token_ids",
            _int_tuple(self.prompt_token_ids, field_name="prompt_token_ids"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "prompt_token_ids": list(self.prompt_token_ids),
            "prefix_hash": self.prefix_hash,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FakeRequest":
        return cls(
            request_id=str(data["request_id"]),
            prompt_token_ids=tuple(data.get("prompt_token_ids") or ()),
            prefix_hash=_optional_str(data.get("prefix_hash")),
        )


@dataclass(frozen=True, slots=True)
class FakeConnectorMetadata:
    request_id: str = "request-0"
    layer_names: tuple[str, ...] = ("layer_0",)
    block_ids: tuple[int, ...] = (0,)
    operation: str = "save"
    connector_instance_id: str = "fake-connector-0"
    prompt_token_ids: tuple[int, ...] | None = None
    prefix_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "layer_names",
            tuple(str(layer_name) for layer_name in self.layer_names),
        )
        object.__setattr__(
            self,
            "block_ids",
            _int_tuple(self.block_ids, field_name="block_ids"),
        )
        if self.prompt_token_ids is not None:
            object.__setattr__(
                self,
                "prompt_token_ids",
                _int_tuple(self.prompt_token_ids, field_name="prompt_token_ids"),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "layer_names": list(self.layer_names),
            "block_ids": list(self.block_ids),
            "prompt_token_ids": (
                None if self.prompt_token_ids is None else list(self.prompt_token_ids)
            ),
            "prefix_hash": self.prefix_hash,
            "operation": self.operation,
            "connector_instance_id": self.connector_instance_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FakeConnectorMetadata":
        prompt_token_ids = data.get("prompt_token_ids")
        return cls(
            request_id=str(data["request_id"]),
            layer_names=tuple(data["layer_names"]),
            block_ids=tuple(data["block_ids"]),
            prompt_token_ids=(
                None if prompt_token_ids is None else tuple(prompt_token_ids)
            ),
            prefix_hash=_optional_str(data.get("prefix_hash")),
            operation=str(data["operation"]),
            connector_instance_id=str(data["connector_instance_id"]),
        )


@dataclass(frozen=True, slots=True)
class FakeAttentionMetadata:
    request_id: str = "request-0"
    layer_names: tuple[str, ...] = ("layer_0",)
    block_ids: tuple[int, ...] = (0,)
    prompt_token_ids: tuple[int, ...] | None = None
    prefix_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "layer_names",
            tuple(str(layer_name) for layer_name in self.layer_names),
        )
        object.__setattr__(
            self,
            "block_ids",
            _int_tuple(self.block_ids, field_name="block_ids"),
        )
        if self.prompt_token_ids is not None:
            object.__setattr__(
                self,
                "prompt_token_ids",
                _int_tuple(self.prompt_token_ids, field_name="prompt_token_ids"),
            )

    def to_connector_metadata(
        self,
        *,
        operation: str,
        connector_instance_id: str,
    ) -> FakeConnectorMetadata:
        return FakeConnectorMetadata(
            request_id=self.request_id,
            layer_names=self.layer_names,
            block_ids=self.block_ids,
            prompt_token_ids=self.prompt_token_ids,
            prefix_hash=self.prefix_hash,
            operation=operation,
            connector_instance_id=connector_instance_id,
        )


@dataclass(frozen=True, slots=True)
class FakeForwardContext:
    request: FakeRequest = field(default_factory=FakeRequest)
    attention_metadata: FakeAttentionMetadata = field(
        default_factory=FakeAttentionMetadata
    )
    operation: str = "save"

    def to_connector_metadata(self, *, connector_instance_id: str) -> FakeConnectorMetadata:
        prompt_token_ids = self.attention_metadata.prompt_token_ids
        if prompt_token_ids is None:
            prompt_token_ids = self.request.prompt_token_ids
        prefix_hash = self.attention_metadata.prefix_hash or self.request.prefix_hash
        return FakeConnectorMetadata(
            request_id=self.request.request_id,
            layer_names=self.attention_metadata.layer_names,
            block_ids=self.attention_metadata.block_ids,
            prompt_token_ids=prompt_token_ids,
            prefix_hash=prefix_hash,
            operation=self.operation,
            connector_instance_id=connector_instance_id,
        )


@dataclass(frozen=True, slots=True)
class FakeSchedulerOutput:
    requests: tuple[FakeRequest, ...] = field(
        default_factory=lambda: (FakeRequest(),)
    )
    attention_metadata: FakeAttentionMetadata = field(
        default_factory=FakeAttentionMetadata
    )
    connector_metadata: tuple[FakeConnectorMetadata, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "requests", tuple(self.requests))
        object.__setattr__(
            self,
            "connector_metadata",
            tuple(self.connector_metadata),
        )

    def build_connector_metadata(
        self,
        *,
        operation: str,
        connector_instance_id: str,
    ) -> tuple[FakeConnectorMetadata, ...]:
        if self.connector_metadata:
            return self.connector_metadata
        return tuple(
            FakeConnectorMetadata(
                request_id=request.request_id,
                layer_names=self.attention_metadata.layer_names,
                block_ids=self.attention_metadata.block_ids,
                prompt_token_ids=request.prompt_token_ids,
                prefix_hash=request.prefix_hash or self.attention_metadata.prefix_hash,
                operation=operation,
                connector_instance_id=connector_instance_id,
            )
            for request in self.requests
        )


def make_fake_kv_caches(
    num_layers: int,
    blocks: int,
    block_size: int,
    heads: int,
    head_dim: int,
    dtype: torch.dtype = torch.float32,
    seed: int = 0,
) -> FakeKVCaches:
    """Return deterministic CPU fake KV caches keyed by ``layer_{index}``."""

    _require_positive_int("num_layers", num_layers)
    _require_positive_int("blocks", blocks)
    _require_positive_int("block_size", block_size)
    _require_positive_int("heads", heads)
    _require_positive_int("head_dim", head_dim)
    if not isinstance(dtype, torch.dtype):
        raise TypeError("dtype must be a torch.dtype")

    generator = torch.Generator(device="cpu")
    generator.manual_seed(int(seed))
    shape = (blocks, block_size, 2, heads, head_dim)
    caches: FakeKVCaches = {}
    for layer_id in range(num_layers):
        if dtype.is_floating_point or dtype.is_complex:
            tensor = torch.randn(shape, generator=generator, dtype=dtype, device="cpu")
        elif dtype == torch.bool:
            tensor = torch.randint(
                0,
                2,
                shape,
                generator=generator,
                dtype=torch.int8,
                device="cpu",
            ).bool()
        else:
            tensor = torch.randint(
                0,
                127,
                shape,
                generator=generator,
                dtype=dtype,
                device="cpu",
            )
        caches[f"layer_{layer_id}"] = tensor.contiguous()
    return caches


def clone_fake_kv_caches(kv_caches: Mapping[str, torch.Tensor]) -> FakeKVCaches:
    """Clone fake KV caches without aliasing the original tensors."""

    return {
        str(layer_name): _validate_kv_layer(kv_layer).detach().clone().contiguous()
        for layer_name, kv_layer in kv_caches.items()
    }


def zero_fake_kv_caches(kv_caches: Mapping[str, torch.Tensor]) -> Mapping[str, torch.Tensor]:
    """Zero fake KV cache tensors in place and return ``kv_caches``."""

    with torch.no_grad():
        for kv_layer in kv_caches.values():
            _validate_kv_layer(kv_layer).zero_()
    return kv_caches


def compare_fake_kv_caches(
    a: Mapping[str, torch.Tensor],
    b: Mapping[str, torch.Tensor],
) -> bool:
    """Return True only when fake KV cache keys and tensors match exactly."""

    if set(a) != set(b):
        return False
    for layer_name in sorted(a):
        a_layer = a[layer_name]
        b_layer = b[layer_name]
        if not torch.is_tensor(a_layer) or not torch.is_tensor(b_layer):
            return False
        if a_layer.shape != b_layer.shape:
            return False
        if a_layer.dtype != b_layer.dtype:
            return False
        if a_layer.device.type != "cpu" or b_layer.device.type != "cpu":
            return False
        if not torch.equal(a_layer, b_layer):
            return False
    return True


def corrupt_one_block(
    kv_caches: FakeKVCaches,
    layer_name: str,
    block_id: int,
) -> FakeKVCaches:
    """Deterministically mutate one element in one fake KV block."""

    if layer_name not in kv_caches:
        raise KeyError(f"unknown fake KV layer: {layer_name}")
    kv_layer = _validate_kv_layer(kv_caches[layer_name])
    block = _normalize_block_id(block_id, num_blocks=int(kv_layer.shape[0]))
    first_index = (block,) + (0,) * (kv_layer.dim() - 1)
    with torch.no_grad():
        if kv_layer.dtype == torch.bool:
            kv_layer[first_index] = torch.logical_not(kv_layer[first_index])
        else:
            kv_layer[first_index] = kv_layer[first_index] + kv_layer.new_tensor(1)
    return kv_caches


def flatten_layer_blocks(
    kv_layer: torch.Tensor,
    block_ids: Iterable[int],
) -> torch.Tensor:
    """Clone selected blocks into a contiguous payload tensor.

    The returned tensor has shape ``[len(block_ids), block_size, 2, heads,
    head_dim]`` and can be passed directly to :func:`write_layer_blocks`.
    """

    kv_layer = _validate_kv_layer(kv_layer)
    ids = _normalize_block_ids(block_ids, num_blocks=int(kv_layer.shape[0]))
    if not ids:
        return kv_layer.new_empty((0, *tuple(kv_layer.shape[1:])))
    index = torch.tensor(ids, dtype=torch.long, device="cpu")
    return kv_layer.index_select(0, index).detach().clone().contiguous()


def write_layer_blocks(
    kv_layer: torch.Tensor,
    block_ids: Iterable[int],
    payload_tensor: torch.Tensor,
) -> torch.Tensor:
    """Copy a contiguous fake payload tensor back into selected layer blocks."""

    kv_layer = _validate_kv_layer(kv_layer)
    ids = _normalize_block_ids(block_ids, num_blocks=int(kv_layer.shape[0]))
    if not torch.is_tensor(payload_tensor):
        raise TypeError("payload_tensor must be a torch.Tensor")
    if payload_tensor.device.type != "cpu":
        raise ValueError("payload_tensor must be on CPU")
    if payload_tensor.dtype != kv_layer.dtype:
        raise ValueError("payload_tensor dtype must match kv_layer dtype")
    expected_shape = (len(ids), *tuple(kv_layer.shape[1:]))
    if tuple(payload_tensor.shape) != expected_shape:
        raise ValueError(
            f"payload_tensor has shape {tuple(payload_tensor.shape)}; "
            f"expected {expected_shape}"
        )
    if ids:
        index = torch.tensor(ids, dtype=torch.long, device="cpu")
        with torch.no_grad():
            kv_layer.index_copy_(0, index, payload_tensor.detach().contiguous())
    return kv_layer


def _validate_kv_layer(kv_layer: torch.Tensor) -> torch.Tensor:
    if not torch.is_tensor(kv_layer):
        raise TypeError("fake KV layer must be a torch.Tensor")
    if kv_layer.device.type != "cpu":
        raise ValueError("fake KV layer must be on CPU")
    if kv_layer.dim() != 5:
        raise ValueError("fake KV layer must have rank 5")
    if int(kv_layer.shape[0]) <= 0:
        raise ValueError("fake KV layer must contain at least one block")
    if int(kv_layer.shape[2]) != 2:
        raise ValueError("fake KV layer dimension 2 must contain key/value slots")
    return kv_layer


def _normalize_block_ids(block_ids: Iterable[int], *, num_blocks: int) -> tuple[int, ...]:
    ids = tuple(_normalize_block_id(block_id, num_blocks=num_blocks) for block_id in block_ids)
    if len(set(ids)) != len(ids):
        raise ValueError("block_ids must be unique")
    return ids


def _normalize_block_id(block_id: int, *, num_blocks: int) -> int:
    block = int(block_id)
    if block < 0 or block >= num_blocks:
        raise ValueError(f"block_id {block} is out of range for {num_blocks} blocks")
    return block


def _int_tuple(values: Iterable[int], *, field_name: str) -> tuple[int, ...]:
    try:
        return tuple(int(value) for value in values)
    except TypeError as exc:
        raise TypeError(f"{field_name} must be an iterable of integers") from exc


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _require_positive_int(name: str, value: int) -> None:
    if int(value) <= 0:
        raise ValueError(f"{name} must be positive")
