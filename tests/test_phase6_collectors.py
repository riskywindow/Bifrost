from __future__ import annotations

import dataclasses
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for source_path in (
    REPO_ROOT / "bifrost_py",
    REPO_ROOT / "integrations" / "lmcache_bifrost",
):
    text = str(source_path)
    if text not in sys.path:
        sys.path.insert(0, text)

from bifrost_serving.collectors import (  # noqa: E402
    BifrostMetricsCollector,
    LMCacheMetricsCollector,
    VLLMMetricsCollector,
)


@dataclasses.dataclass(frozen=True)
class FakeStats:
    object_count: int
    total_logical_bytes: int
    total_bytes_on_disk: int = 0


@dataclasses.dataclass(frozen=True)
class FakeObject:
    object_id: str
    object_type: str
    state: str
    byte_length: int
    engine_name: str | None = None
    integration_name: str | None = None


class FakeBifrostClient:
    def __init__(self, stats: FakeStats, objects: list[FakeObject]) -> None:
        self._stats = stats
        self._objects = objects
        self.connected = False
        self.closed = False

    def connect(self) -> "FakeBifrostClient":
        self.connected = True
        return self

    def close(self) -> None:
        self.closed = True

    def stats(self) -> FakeStats:
        return self._stats

    def list_objects(self, **_filters: Any) -> list[FakeObject]:
        return self._objects


def test_bifrost_collector_works_against_fake_client_and_delta(tmp_path: Path) -> None:
    connector_jsonl = tmp_path / "connector.jsonl"
    connector_jsonl.write_text(
        "\n".join(
            [
                json.dumps({"event": "connector_put_completed", "operation": "put", "bytes": 10}),
                json.dumps({"event": "connector_exists", "operation": "exists"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    clients = iter(
        [
            FakeBifrostClient(
                FakeStats(object_count=2, total_logical_bytes=100),
                [
                    FakeObject(
                        "opaque-0",
                        "opaque_engine_blob",
                        "committed",
                        50,
                        engine_name="lmcache",
                        integration_name="lmcache_bifrost_remote_storage",
                    )
                ],
            ),
            FakeBifrostClient(
                FakeStats(object_count=5, total_logical_bytes=190),
                [
                    FakeObject(
                        "opaque-0",
                        "opaque_engine_blob",
                        "committed",
                        50,
                        engine_name="lmcache",
                        integration_name="lmcache_bifrost_remote_storage",
                    ),
                    FakeObject(
                        "opaque-1",
                        "opaque_engine_blob",
                        "committed",
                        90,
                        engine_name="lmcache",
                        integration_name="lmcache_bifrost_remote_storage",
                    ),
                ],
            ),
        ]
    )

    collector = BifrostMetricsCollector(
        client_factory=lambda: next(clients),
        connector_metrics_jsonl_path=connector_jsonl,
    )
    before = collector.snapshot_before()

    with connector_jsonl.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"event": "connector_get_completed", "operation": "get", "bytes": 7})
            + "\n"
        )

    after = collector.snapshot_after()
    delta = collector.delta()

    assert before["status"] == "ok"
    assert after["stats"]["object_count"] == 5
    assert after["opaque_lmcache_object_count"] == 2
    assert after["fsck"]["status"] == "skipped"
    assert delta is not None
    assert delta["object_count"] == 3
    assert delta["bytes_stored"] == 90
    assert delta["opaque_lmcache_object_count"] == 1
    assert delta["get_count"] == 1
    assert delta["bytes_get"] == 7


def test_lmcache_unavailable_does_not_fail() -> None:
    snapshot = LMCacheMetricsCollector().snapshot()

    assert snapshot["collector"] == "lmcache"
    assert snapshot["status"] == "unavailable"


def test_vllm_unavailable_does_not_fail() -> None:
    snapshot = VLLMMetricsCollector().snapshot()

    assert snapshot["collector"] == "vllm"
    assert snapshot["status"] == "unavailable"


def test_lmcache_raw_json_is_preserved_and_known_fields_are_defensive() -> None:
    with JsonServer({"cache": {"hit_count": 3, "miss_count": 2}, "version_field": "x"}) as url:
        snapshot = LMCacheMetricsCollector(metrics_url=url).snapshot()

    assert snapshot["status"] == "ok"
    assert snapshot["raw"]["cache"]["hit_count"] == 3
    assert snapshot["metrics"]["hit_count"] == 3
    assert snapshot["metrics"]["miss_count"] == 2
    assert snapshot["metrics"]["remote_storage_hits"] is None


def test_vllm_raw_json_is_preserved() -> None:
    raw = {"vllm": {"num_requests_running": 1, "gpu_cache_usage": 0.25}}
    with JsonServer(raw) as url:
        snapshot = VLLMMetricsCollector(metrics_url=url).snapshot()

    assert snapshot["status"] == "ok"
    assert snapshot["raw"] == raw
    assert snapshot["metrics"]["running_requests"] == 1
    assert snapshot["metrics"]["gpu_cache_usage"] == 0.25


class JsonServer:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def __enter__(self) -> str:
        payload = self.payload

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}/metrics"

    def __exit__(self, *_exc: object) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)
