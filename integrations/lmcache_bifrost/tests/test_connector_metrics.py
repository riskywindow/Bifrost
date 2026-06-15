from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from lmcache_bifrost.config import BifrostLMCacheConfig
from lmcache_bifrost.connector import BifrostRemoteConnector
from lmcache_bifrost.errors import MemoryObjSerializationError
from tests.fakes import FakeCacheEngineKey, FakeMemoryObj
from tests.test_connector_fake import FakeBifrostClient


def test_metrics_increment_on_put_get_exists_and_list() -> None:
    async def run() -> None:
        connector = _connector(FakeBifrostClient())
        key = FakeCacheEngineKey("tiny", "metrics", (1, 2, 3))
        memory_obj = FakeMemoryObj(b"observed-lmcache-bytes")

        await connector.put(key, memory_obj)
        assert await connector.exists(key) is True
        assert await connector.get(key) == memory_obj
        assert await connector.list()

        metrics = connector.metrics_snapshot()
        assert metrics["put_count"] == 1
        assert metrics["exists_count"] == 1
        assert metrics["get_count"] == 1
        assert metrics["list_count"] == 1
        assert metrics["put_error_count"] == 0
        assert metrics["get_error_count"] == 0
        assert metrics["bytes_put"] > 0
        assert metrics["bytes_get"] == metrics["bytes_put"]
        assert metrics["total_put_ms"] >= 0
        assert metrics["total_get_ms"] >= 0

    asyncio.run(run())


def test_serialization_failure_increments_error_counter() -> None:
    async def run() -> None:
        connector = BifrostRemoteConnector(
            BifrostLMCacheConfig(endpoint="fake", allow_pickle_fallback=False),
            client=FakeBifrostClient(),
        )
        key = FakeCacheEngineKey("tiny", "serialization-failure", (4, 5, 6))

        with pytest.raises(MemoryObjSerializationError):
            await connector.put(key, FakeMemoryObj(b"payload"))

        metrics = connector.metrics_snapshot()
        assert metrics["put_count"] == 1
        assert metrics["put_error_count"] == 1
        assert metrics["serialization_error_count"] == 1
        assert metrics["bytes_put"] == 0

    asyncio.run(run())


def test_jsonl_logs_are_valid_json(tmp_path: Path) -> None:
    async def run() -> None:
        log_path = tmp_path / "connector.jsonl"
        connector = _connector(FakeBifrostClient(), metrics_jsonl_path=str(log_path))
        key = FakeCacheEngineKey("tiny", "jsonl", (7, 8, 9))
        memory_obj = FakeMemoryObj(b"jsonl-observed-bytes")

        await connector.put(key, memory_obj)
        assert await connector.exists(key) is True
        assert await connector.get(key) == memory_obj

        events = [json.loads(line) for line in log_path.read_text().splitlines()]
        assert {event["event"] for event in events} >= {
            "connector_put_started",
            "connector_put_completed",
            "connector_exists",
            "connector_get_started",
            "connector_get_completed",
        }
        for event in events:
            assert isinstance(event["timestamp_unix_ms"], int)
            assert isinstance(event["operation"], str)
        completed = [event for event in events if event["event"].endswith("_completed")]
        assert all("duration_ms" in event for event in completed)

    asyncio.run(run())


def _connector(
    client: FakeBifrostClient,
    *,
    metrics_jsonl_path: str | None = None,
) -> BifrostRemoteConnector:
    return BifrostRemoteConnector(
        BifrostLMCacheConfig(
            endpoint="fake",
            allow_pickle_fallback=True,
            metrics_jsonl_path=metrics_jsonl_path,
        ),
        client=client,
    )
