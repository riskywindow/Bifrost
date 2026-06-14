"""Phase 5 LMCache integration error types."""

from __future__ import annotations


class BifrostLMCacheError(Exception):
    """Base class for deterministic LMCache integration failures."""


class BifrostLMCacheSerializationError(BifrostLMCacheError):
    """Raised when LMCache-owned bytes cannot be serialized or restored."""


class BifrostLMCacheValidationError(BifrostLMCacheError):
    """Raised when an opaque LMCache blob fails BIFROST validation."""


class BifrostLMCacheStoreError(BifrostLMCacheError):
    """Raised when the BIFROST store/client operation fails."""


class BifrostLMCacheNotFoundError(BifrostLMCacheError):
    """Raised when a requested LMCache opaque object is absent."""


class KeyCodecError(BifrostLMCacheError):
    """Raised when a CacheEngineKey cannot be represented or hashed safely."""


class MemoryObjSerializationError(BifrostLMCacheSerializationError):
    """Raised when an LMCache MemoryObj cannot be serialized safely."""


class MemoryObjDeserializationError(BifrostLMCacheSerializationError):
    """Raised when payload bytes cannot be deserialized into a MemoryObj."""


class OpaqueBlobValidationError(BifrostLMCacheValidationError):
    """Raised when generated opaque blob metadata fails Phase 1 validation."""


class ConnectorConfigurationError(BifrostLMCacheError):
    """Raised for invalid BIFROST LMCache integration configuration."""


__all__ = [
    "BifrostLMCacheError",
    "BifrostLMCacheNotFoundError",
    "BifrostLMCacheSerializationError",
    "BifrostLMCacheStoreError",
    "BifrostLMCacheValidationError",
    "ConnectorConfigurationError",
    "KeyCodecError",
    "MemoryObjDeserializationError",
    "MemoryObjSerializationError",
    "OpaqueBlobValidationError",
]
