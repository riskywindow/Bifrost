"""Fake LMCache objects for CI-safe Phase 5 tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class FakeCacheEngineKey:
    model_id: str
    block_hash: str
    tokens: tuple[int, ...]
    extra: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class FakeMemoryObj:
    payload: bytes
    dtype: str = "float16"
    shape: tuple[int, ...] = (1,)


@dataclass(frozen=True, slots=True)
class FakeLMCacheConfig:
    remote_url: str = "bifrost://127.0.0.1:8765"
    chunk_size: int = 1024 * 1024
    extra_config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FakeLMCacheMetadata:
    model_name: str = "fake-model"
    world_size: int = 1
    worker_id: int = 0


@dataclass(frozen=True, slots=True)
class FakeConnectorContext:
    config: FakeLMCacheConfig = field(default_factory=FakeLMCacheConfig)
    metadata: FakeLMCacheMetadata = field(default_factory=FakeLMCacheMetadata)
    remote_url: str = "bifrost://127.0.0.1:8765"
