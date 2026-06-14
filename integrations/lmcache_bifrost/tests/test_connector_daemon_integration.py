from __future__ import annotations

import asyncio
import json
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Iterator

import pytest

from lmcache_bifrost.config import BifrostLMCacheConfig
from lmcache_bifrost.connector import BifrostRemoteConnector
from lmcache_bifrost.key_codec import opaque_engine_key_hash
from tests.fakes import FakeCacheEngineKey, FakeMemoryObj

REPO_ROOT = Path(__file__).resolve().parents[3]


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

    def restart(self) -> None:
        self.stop()
        self.start()

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


def test_connector_roundtrips_through_daemon_and_fsck_clean(
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    asyncio.run(_roundtrip_with_restart(daemon, binaries["store"]))


async def _roundtrip_with_restart(daemon: Daemon, store_bin: Path) -> None:
    key = FakeCacheEngineKey("tiny", "daemon", (11, 12, 13))
    memory_obj = FakeMemoryObj(b"daemon-backed-lmcache-bytes", shape=(1, 3, 4))
    config = BifrostLMCacheConfig(
        endpoint=daemon.endpoint,
        allow_pickle_fallback=True,
        timeout_seconds=5,
    )

    writer = BifrostRemoteConnector(config)
    try:
        await writer.put(key, memory_obj)
        assert await writer.exists(key) is True
    finally:
        await writer.close()

    daemon.restart()

    reader = BifrostRemoteConnector(config)
    try:
        assert await reader.exists(key) is True
        assert reader.exists_sync(key) is True
        assert await reader.get(key) == memory_obj
        assert f"lmcache:{opaque_engine_key_hash(key)}" in await reader.list()
    finally:
        await reader.close()

    fsck = _fsck_json(daemon.endpoint, store_bin)
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
