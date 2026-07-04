from __future__ import annotations

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
SCRIPT = REPO_ROOT / "examples" / "lmcache_bifrost" / "plugin_roundtrip.py"
CONFIGS = [
    REPO_ROOT
    / "integrations"
    / "lmcache_bifrost"
    / "examples"
    / "lmcache_config_bifrost.yaml",
    REPO_ROOT
    / "integrations"
    / "lmcache_bifrost"
    / "examples"
    / "lmcache_config_bifrost_pickle_dev.yaml",
]


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


def test_script_json_output_parses_when_daemon_missing() -> None:
    result = run_script("--endpoint", f"127.0.0.1:{_free_port()}", "--json")

    assert result.returncode != 0
    summary = json.loads(result.stdout)
    assert summary["status"] == "fail"
    assert summary["endpoint"].startswith("127.0.0.1:")


def test_script_passes_with_fake_objects_and_local_daemon(daemon: Daemon) -> None:
    result = run_script(
        "--endpoint",
        daemon.endpoint,
        "--allow-pickle-fallback",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "pass"
    assert summary["put_success"] is True
    assert summary["exists_result"] is True
    assert summary["get_success"] is True
    assert summary["list_count"] >= 1
    assert summary["payload_roundtrip_match"] is True
    assert summary["opaque_engine_key_hash"].startswith("blake3:")
    assert summary["object_id"].startswith("bifrost://object/blake3/")


def test_script_exits_nonzero_when_daemon_missing() -> None:
    result = run_script(
        "--endpoint",
        f"127.0.0.1:{_free_port()}",
        "--allow-pickle-fallback",
    )

    assert result.returncode != 0
    assert "result: fail" in result.stderr


def test_script_refuses_pickle_fallback_unless_explicit_for_fake_objects(
    daemon: Daemon,
) -> None:
    result = run_script("--endpoint", daemon.endpoint, "--json")

    assert result.returncode != 0
    summary = json.loads(result.stdout)
    assert summary["status"] == "fail"
    assert "pickle fallback is disabled" in summary["error"]


def test_docs_config_yaml_parses() -> None:
    yaml = pytest.importorskip("yaml")

    for path in CONFIGS:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "remote_storage_plugins" in parsed
        extra = parsed["extra_config"]
        assert extra["remote_storage_plugin.bifrost.module_path"] == "lmcache_bifrost.adapter"
        assert extra["remote_storage_plugin.bifrost.class_name"] == "BifrostConnectorAdapter"
        assert str(parsed["remote_url"]).startswith(("bifrost://", "plugin://"))


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(REPO_ROOT / "bifrost_py"),
            str(REPO_ROOT / "integrations" / "lmcache_bifrost"),
        ]
    )
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _find_binary(name: str) -> Path | None:
    for candidate in (
        REPO_ROOT / "bifrostd" / "target" / "debug" / name,
        REPO_ROOT / "target" / "debug" / name,
    ):
        if candidate.exists():
            return candidate
    found = shutil.which(name)
    return Path(found) if found else None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
