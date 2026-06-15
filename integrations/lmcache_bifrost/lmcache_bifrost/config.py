"""Configuration for the BIFROST LMCache opaque blob codec."""

from __future__ import annotations

from dataclasses import dataclass

from lmcache_bifrost.errors import ConnectorConfigurationError


@dataclass(frozen=True, slots=True)
class BifrostLMCacheConfig:
    endpoint: str = "127.0.0.1:8765"
    chunk_size: int = 1024 * 1024
    allow_pickle_fallback: bool = False
    key_repr_version: str = "lmcache_key_repr.v1"
    engine_name: str = "lmcache"
    integration_name: str = "lmcache_bifrost_remote_storage"
    timeout_seconds: float = 5.0
    strict_validation: bool = True
    metrics_jsonl_path: str | None = None

    def __post_init__(self) -> None:
        if not self.endpoint:
            raise ConnectorConfigurationError("endpoint must be non-empty")
        if self.chunk_size <= 0:
            raise ConnectorConfigurationError("chunk_size must be positive")
        if not self.key_repr_version:
            raise ConnectorConfigurationError("key_repr_version must be non-empty")
        if not self.engine_name:
            raise ConnectorConfigurationError("engine_name must be non-empty")
        if not self.integration_name:
            raise ConnectorConfigurationError("integration_name must be non-empty")
        if self.timeout_seconds <= 0:
            raise ConnectorConfigurationError("timeout_seconds must be positive")
        if self.metrics_jsonl_path is not None and not str(self.metrics_jsonl_path):
            raise ConnectorConfigurationError("metrics_jsonl_path must be non-empty")


__all__ = ["BifrostLMCacheConfig"]
