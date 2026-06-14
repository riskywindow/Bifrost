"""Synchronous wrapper for the async BIFROST daemon client."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any, Coroutine, TypeVar

from .async_client import BifrostAsyncClient
from .models import BifrostClientConfig, ObjectSummary, PutResult, StoreStats, StoredObject

T = TypeVar("T")


class BifrostClient:
    """Sync facade over `BifrostAsyncClient`.

    The wrapper owns a private background event loop. This makes it usable from
    synchronous LMCache methods, including threads that already have another
    event loop active. It should be closed explicitly to stop the loop thread.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        config: BifrostClientConfig | None = None,
    ) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="bifrost-client-loop",
            daemon=True,
        )
        self._thread.start()
        self._client = BifrostAsyncClient(endpoint=endpoint, config=config)
        self._closed = False

    def connect(self) -> "BifrostClient":
        self._run(self._client.connect())
        return self

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._run(self._client.close())
        finally:
            self._closed = True
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)

    def ping(self) -> bool:
        return self._run(self._client.ping())

    def put_object(
        self,
        metadata: dict[str, Any],
        payload: bytes,
        chunk_size: int = 256 * 1024,
    ) -> PutResult:
        return self._run(self._client.put_object(metadata, payload, chunk_size))

    def has_object(self, object_id: str) -> bool:
        return self._run(self._client.has_object(object_id))

    def get_object(self, object_id: str) -> StoredObject:
        return self._run(self._client.get_object(object_id))

    def query_by_opaque_key_hash(
        self,
        engine_name: str,
        integration_name: str,
        opaque_engine_key_hash: str,
    ) -> list[ObjectSummary]:
        return self._run(
            self._client.query_by_opaque_key_hash(
                engine_name,
                integration_name,
                opaque_engine_key_hash,
            )
        )

    def list_objects(self, **filters: Any) -> list[ObjectSummary]:
        return self._run(self._client.list_objects(**filters))

    def stats(self) -> StoreStats:
        return self._run(self._client.stats())

    def _run(self, coroutine: Coroutine[Any, Any, T]) -> T:
        if self._closed:
            raise RuntimeError("BIFROST client is closed")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result()
        except concurrent.futures.CancelledError:
            raise RuntimeError("BIFROST client operation was cancelled")

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        self._loop.close()

    def __enter__(self) -> "BifrostClient":
        return self.connect()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
