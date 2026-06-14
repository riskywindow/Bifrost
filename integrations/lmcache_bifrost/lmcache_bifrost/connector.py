"""BIFROST LMCache remote connector skeleton."""

from __future__ import annotations

from typing import Any

from lmcache_bifrost.config import BifrostLMCacheConfig
from lmcache_bifrost.errors import ConnectorConfigurationError
from lmcache_bifrost.lmcache_compat import RemoteConnector as LMCacheRemoteConnector

_BaseRemoteConnector = LMCacheRemoteConnector or object


class BifrostRemoteConnector(_BaseRemoteConnector):  # type: ignore[misc]
    """LMCache remote connector configured by `BifrostConnectorAdapter`.

    The Phase 5 adapter task only constructs the connector. Object operations
    are intentionally left for the remote connector implementation step.
    """

    def __init__(
        self,
        config: BifrostLMCacheConfig,
        *,
        context: object | None = None,
        client: object | None = None,
    ) -> None:
        self.config = config
        self.context = context
        self.client = client
        self.closed = False

    async def exists(self, key: object) -> bool:
        del key
        self._ensure_open()
        raise NotImplementedError("BifrostRemoteConnector.exists is not implemented yet")

    def exists_sync(self, key: object) -> bool:
        del key
        self._ensure_open()
        raise NotImplementedError(
            "BifrostRemoteConnector.exists_sync is not implemented yet"
        )

    async def get(self, key: object) -> object | None:
        del key
        self._ensure_open()
        raise NotImplementedError("BifrostRemoteConnector.get is not implemented yet")

    async def put(self, key: object, memory_obj: object) -> None:
        del key, memory_obj
        self._ensure_open()
        raise NotImplementedError("BifrostRemoteConnector.put is not implemented yet")

    async def list(self) -> list[str]:
        self._ensure_open()
        raise NotImplementedError("BifrostRemoteConnector.list is not implemented yet")

    async def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result
        self.closed = True

    def _ensure_open(self) -> None:
        if self.closed:
            raise ConnectorConfigurationError("BIFROST connector is closed")


__all__ = ["BifrostRemoteConnector"]
