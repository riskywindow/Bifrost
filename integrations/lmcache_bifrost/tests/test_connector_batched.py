from __future__ import annotations

import asyncio

import pytest

from lmcache_bifrost.errors import BifrostLMCacheStoreError
from tests.fakes import FakeCacheEngineKey, FakeMemoryObj
from tests.test_connector_fake import FakeBifrostClient, _connector


def test_fake_connector_ping_returns_true_when_daemon_is_alive() -> None:
    async def run() -> None:
        connector = _connector(FakeBifrostClient())

        assert connector.support_ping() is True
        assert await connector.ping() is True

    asyncio.run(run())


def test_fake_connector_batched_contains_returns_expected_booleans() -> None:
    async def run() -> None:
        connector = _connector(FakeBifrostClient())
        first = FakeCacheEngineKey("tiny", "first", (1, 2, 3))
        second = FakeCacheEngineKey("tiny", "second", (4, 5, 6))
        missing = FakeCacheEngineKey("tiny", "missing", (7, 8, 9))

        await connector.put(first, FakeMemoryObj(b"first"))
        await connector.put(second, FakeMemoryObj(b"second"))

        assert connector.support_batched_contains() is True
        assert await connector.batched_contains([first, missing, second]) == [
            True,
            False,
            True,
        ]

    asyncio.run(run())


def test_fake_connector_batched_get_returns_objects_and_none_for_missing() -> None:
    async def run() -> None:
        connector = _connector(FakeBifrostClient())
        first = FakeCacheEngineKey("tiny", "first", (1, 2, 3))
        second = FakeCacheEngineKey("tiny", "second", (4, 5, 6))
        missing = FakeCacheEngineKey("tiny", "missing", (7, 8, 9))
        first_obj = FakeMemoryObj(b"first", shape=(1, 3))
        second_obj = FakeMemoryObj(b"second", shape=(2, 3))

        await connector.put(first, first_obj)
        await connector.put(second, second_obj)

        assert connector.support_batched_get() is True
        assert await connector.batched_get([first, missing, second]) == [
            first_obj,
            None,
            second_obj,
        ]

    asyncio.run(run())


def test_fake_connector_batched_put_stores_multiple_objects() -> None:
    async def run() -> None:
        connector = _connector(FakeBifrostClient())
        first = FakeCacheEngineKey("tiny", "first", (1, 2, 3))
        second = FakeCacheEngineKey("tiny", "second", (4, 5, 6))
        first_obj = FakeMemoryObj(b"first")
        second_obj = FakeMemoryObj(b"second")

        assert connector.support_batched_put() is True
        await connector.batched_put([(first, first_obj), (second, second_obj)])

        assert await connector.get(first) == first_obj
        assert await connector.get(second) == second_obj

    asyncio.run(run())


def test_fake_connector_batched_put_failure_has_reason_code() -> None:
    async def run() -> None:
        client = FailingPutClient(fail_on_call=2)
        connector = _connector(client)
        first = FakeCacheEngineKey("tiny", "first", (1, 2, 3))
        second = FakeCacheEngineKey("tiny", "second", (4, 5, 6))

        with pytest.raises(BifrostLMCacheStoreError, match="batched_put_failed:index=1"):
            await connector.batched_put(
                [
                    (first, FakeMemoryObj(b"first")),
                    (second, FakeMemoryObj(b"second")),
                ]
            )

        assert await connector.get(first) == FakeMemoryObj(b"first")
        assert await connector.get(second) is None

    asyncio.run(run())


class FailingPutClient(FakeBifrostClient):
    def __init__(self, *, fail_on_call: int) -> None:
        super().__init__()
        self.fail_on_call = fail_on_call
        self.put_calls = 0

    def put_object(self, *args: object, **kwargs: object) -> object:
        self.put_calls += 1
        if self.put_calls == self.fail_on_call:
            raise BifrostLMCacheStoreError("store_commit_error")
        return super().put_object(*args, **kwargs)  # type: ignore[arg-type]
