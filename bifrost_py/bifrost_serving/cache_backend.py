"""Cache backend protocol for Phase 6 fake serving."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CacheBackend(Protocol):
    async def lookup(self, key: str) -> object | None:
        """Return the cached object for key, or None on a miss."""

    async def store(self, key: str, value: object) -> None:
        """Store a cache value for key."""

    async def exists(self, key: str) -> bool:
        """Return whether key is available as a cache hit."""

    def metrics_snapshot(self) -> dict[str, Any]:
        """Return backend metrics without fabricating connector counters."""

    async def close(self) -> None:
        """Release backend resources."""


__all__ = ["CacheBackend"]
