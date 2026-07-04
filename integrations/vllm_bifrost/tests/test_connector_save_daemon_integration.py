from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BIFROST_PY = REPO_ROOT / "bifrost_py"
if str(BIFROST_PY) not in sys.path:
    sys.path.insert(0, str(BIFROST_PY))

from bifrost_client import BifrostClient, BifrostClientConfig
from bifrost_kv.validate import validate_object
from vllm_bifrost.config import ENGINE_NAME, INTEGRATION_NAME, KV_CACHE_FORMAT
from vllm_bifrost.connector import BifrostKVConnector
from vllm_bifrost.fakes import (
    FakeAttentionMetadata,
    FakeKVCacheConfig,
    FakeVllmConfig,
    make_fake_kv_caches,
)


class Daemon:
    def __init__(self, binary: Path, endpoint: str, spool: Path) -> None:
        self.binary = binary
        self.endpoint = endpoint
        self.spool = spool
        self.process: subprocess.Popen[str] | None = None

    def start(self) -> None:
        self.spool.mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            [
                str(self.binary),
                "--listen",
                self.endpoint,
                "--spool",
                str(self.spool),
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._wait_until_ready()

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.process = None

    def _wait_until_ready(self) -> None:
        deadline = time.time() + 10
        host, port_text = self.endpoint.rsplit(":", 1)
        last_error: OSError | None = None
        while time.time() < deadline:
            if self.process is not None and self.process.poll() is not None:
                stderr = self.process.stderr.read() if self.process.stderr else ""
                raise RuntimeError(f"daemon exited early: {stderr}")
            try:
                with socket.create_connection((host, int(port_text)), timeout=0.2):
                    return
            except OSError as exc:
                last_error = exc
                time.sleep(0.05)
        raise RuntimeError(f"daemon did not start: {last_error}")


@pytest.fixture()
def binaries() -> dict[str, Path]:
    found = {
        "daemon": _find_binary("bifrost-daemon"),
        "store": _find_binary("bifrost-store"),
    }
    if found["daemon"] is None or found["store"] is None:
        pytest.skip(
            "Rust bifrost-daemon/bifrost-store unavailable; run "
            "`cargo build --manifest-path bifrostd/Cargo.toml --bins`"
        )
    return {key: value for key, value in found.items() if value is not None}


@pytest.fixture()
def daemon(tmp_path: Path, binaries: dict[str, Path]) -> Iterator[Daemon]:
    daemon = Daemon(
        binary=binaries["daemon"],
        endpoint=f"127.0.0.1:{_free_port()}",
        spool=tmp_path / "spool",
    )
    daemon.start()
    try:
        yield daemon
    finally:
        daemon.stop()


def test_connector_saves_multiple_fake_layers_to_daemon(
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    kv_config = FakeKVCacheConfig(num_layers=2, num_blocks=4, block_size=3)
    caches = make_fake_kv_caches(2, 4, 3, 2, 4, seed=101)
    connector = BifrostKVConnector(
        FakeVllmConfig(kv_cache_config=kv_config),
        config={
            "endpoint": daemon.endpoint,
            "timeout_seconds": 5,
            "chunk_size": 128,
            "connector_instance_id": "daemon-save-connector",
        },
    )

    try:
        connector.register_kv_caches(caches)
        for layer_name in ("layer_0", "layer_1"):
            connector.save_kv_layer(
                layer_name,
                caches[layer_name],
                FakeAttentionMetadata(
                    request_id="request-daemon-save",
                    layer_names=(layer_name,),
                    block_ids=(0, 2),
                ),
            )
        connector.wait_for_save()

        saved = connector.saved_objects
        assert len(saved) == 2
        stats = connector.get_kv_connector_stats()
        assert stats["save_success_count"] == 2
        assert stats["objects_saved"] == 2
        assert stats["bytes_saved"] > 0

        client = BifrostClient(
            config=BifrostClientConfig(
                endpoint=daemon.endpoint,
                timeout_seconds=5,
                default_chunk_size=128,
            )
        )
        try:
            listed = client.list_objects(engine_name=ENGINE_NAME)
            listed_ids = {item.object_id for item in listed}
            assert {item["object_id"] for item in saved}.issubset(listed_ids)

            for record in saved:
                matches = client.query_by_opaque_key_hash(
                    ENGINE_NAME,
                    INTEGRATION_NAME,
                    record["blob_key_hash"],
                )
                assert [item.object_id for item in matches] == [record["object_id"]]
                stored = client.get_object(record["object_id"])
                validation = validate_object(stored.metadata, stored.payload, None)
                assert validation.status == "accepted"
                assert validation.object_id == record["object_id"]
                assert stored.metadata["engine_profile"]["engine_name"] == ENGINE_NAME
                assert stored.metadata["engine_profile"]["integration_name"] == (
                    INTEGRATION_NAME
                )
                assert stored.metadata["engine_profile"]["kv_cache_format"] == (
                    KV_CACHE_FORMAT
                )
                assert stored.metadata["opaque_engine_profile"][
                    "engine_key_hash"
                ] == record["blob_key_hash"]
        finally:
            client.close()
    finally:
        connector.shutdown()

    fsck = _fsck_json(daemon.endpoint, binaries["store"])
    assert fsck["status"] == "clean"
    assert fsck["findings"] == []


def _fsck_json(endpoint: str, store_bin: Path) -> dict[str, Any]:
    result = subprocess.run(
        [str(store_bin), "fsck", "--endpoint", endpoint, "--check", "--json"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _find_binary(name: str) -> Path | None:
    candidates = [
        REPO_ROOT / "bifrostd" / "target" / "debug" / name,
        REPO_ROOT / "target" / "debug" / name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
