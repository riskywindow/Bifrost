from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BIFROST_PY = REPO_ROOT / "bifrost_py"
if str(BIFROST_PY) not in sys.path:
    sys.path.insert(0, str(BIFROST_PY))

from bifrost_client import BifrostAsyncClient, BifrostClient
from bifrost_client.models import BifrostClientConfig


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
def daemon_binary() -> Path:
    binary = _find_binary("bifrost-daemon")
    if binary is None:
        pytest.skip(
            "Rust bifrost-daemon unavailable; run "
            "`cargo build --manifest-path bifrostd/Cargo.toml --bins`"
        )
    return binary


@pytest.fixture()
def daemon(tmp_path: Path, daemon_binary: Path) -> Iterator[Daemon]:
    daemon = Daemon(
        binary=daemon_binary,
        endpoint=f"127.0.0.1:{_free_port()}",
        spool=tmp_path / "spool",
    )
    daemon.start()
    try:
        yield daemon
    finally:
        daemon.stop()


def test_python_client_roundtrips_opaque_object(daemon: Daemon) -> None:
    asyncio.run(_async_roundtrip(daemon.endpoint))


def test_sync_client_has_object_uses_private_loop(daemon: Daemon) -> None:
    metadata, payload = _opaque_fixture()
    config = BifrostClientConfig(endpoint=daemon.endpoint, timeout_seconds=5)
    client = BifrostClient(config=config)
    try:
        result = client.put_object(metadata, payload, chunk_size=1024)
        assert result.stored is True
        assert client.has_object(metadata["object_id"]) is True
    finally:
        client.close()


async def _async_roundtrip(endpoint: str) -> None:
    metadata, payload = _opaque_fixture()
    client = BifrostAsyncClient(config=BifrostClientConfig(endpoint=endpoint, timeout_seconds=5))
    await client.connect()
    try:
        assert await client.ping() is True
        put = await client.put_object(metadata, payload, chunk_size=1024)
        assert put.object_id == metadata["object_id"]
        assert put.stored is True
        assert put.verified is True

        assert await client.has_object(metadata["object_id"]) is True
        stored = await client.get_object(metadata["object_id"])
        assert stored.metadata == metadata
        assert stored.payload == payload

        engine = metadata["engine_profile"]
        opaque = metadata["opaque_engine_profile"]
        matches = await client.query_by_opaque_key_hash(
            engine_name=engine["engine_name"],
            integration_name=engine["integration_name"],
            opaque_engine_key_hash=opaque["engine_key_hash"],
        )
        assert [item.object_id for item in matches] == [metadata["object_id"]]
        assert matches[0].integration_name == engine["integration_name"]

        listed = await client.list_objects(
            engine_name=engine["engine_name"],
            opaque_engine_key_hash=opaque["engine_key_hash"],
        )
        assert [item.object_id for item in listed] == [metadata["object_id"]]

        stats = await client.stats()
        assert stats.object_count >= 1
        assert stats.verified_count >= 1
        assert stats.total_logical_bytes >= len(payload)
    finally:
        await client.close()


def _opaque_fixture() -> tuple[dict[str, object], bytes]:
    metadata = json.loads(
        (REPO_ROOT / "fixtures" / "opaque_valid" / "lmcache_blob.meta.json").read_text(
            encoding="utf-8"
        )
    )
    payload = (REPO_ROOT / "fixtures" / "opaque_valid" / "lmcache_blob.payload.bin").read_bytes()
    return metadata, payload


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
