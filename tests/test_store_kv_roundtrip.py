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
BIFROST_PY = REPO_ROOT / "bifrost_py"
SCRIPT = REPO_ROOT / "examples" / "tiny_transformer" / "store_kv_roundtrip.py"

sys.path.insert(0, str(BIFROST_PY))

from bifrost_kv.validate import validate_object
from bifrost_model.store_roundtrip import (
    complete_store_roundtrip,
    get_native_pages,
    inspect_native_pages,
    prepare_store_roundtrip,
    put_native_pages,
    run_store_roundtrip,
    write_native_page_files,
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
    paths = {
        "daemon": _find_binary("bifrost-daemon"),
        "xfer": _find_binary("bifrost-xfer"),
        "store": _find_binary("bifrost-store"),
    }
    missing = [name for name, path in paths.items() if path is None]
    if missing:
        pytest.skip(
            "Rust binaries unavailable; run "
            "`cargo build --manifest-path bifrostd/Cargo.toml --bins`"
        )
    return {name: path for name, path in paths.items() if path is not None}


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


def test_store_roundtrip_passes_and_fetches_every_page(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    work_dir = tmp_path / "roundtrip"

    summary = run_store_roundtrip(
        endpoint=daemon.endpoint,
        prompt="1 2 3 4 5",
        decode_tokens=4,
        block_size=2,
        seed=1234,
        work_dir=work_dir,
        xfer_bin=binaries["xfer"],
        store_bin=binaries["store"],
    )

    assert summary["status"] == "pass"
    assert summary["page_count"] == 6
    assert summary["put_success_count"] == summary["page_count"]
    assert summary["get_success_count"] == summary["page_count"]
    assert len(summary["object_ids"]) == summary["page_count"]
    assert len(set(summary["object_ids"])) == summary["page_count"]
    assert summary["continuation_match"] is True
    assert summary["baseline_continuation"] == summary["rehydrated_continuation"]
    assert summary["logit_max_abs_error"] <= 1e-6

    _assert_get_payloads_validate(work_dir, summary["page_count"])


def test_store_roundtrip_script_json_output(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BIFROST_PY)
    env["PATH"] = f"{binaries['xfer'].parent}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--endpoint",
            daemon.endpoint,
            "--prompt",
            "1 2 3 4 5",
            "--decode-tokens",
            "4",
            "--block-size",
            "2",
            "--seed",
            "1234",
            "--work-dir",
            str(tmp_path / "script-work"),
            "--json",
        ],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["status"] == "pass"
    assert summary["put_success_count"] == summary["page_count"]
    assert summary["get_success_count"] == summary["page_count"]
    assert summary["continuation_match"] is True


def test_daemon_restart_between_put_and_get_still_roundtrips(
    tmp_path: Path,
    daemon: Daemon,
    binaries: dict[str, Path],
) -> None:
    prepared = prepare_store_roundtrip(
        prompt="1 2 3 4 5",
        decode_tokens=4,
        block_size=2,
        seed=1234,
    )
    page_files = write_native_page_files(prepared.pages, tmp_path / "manual")

    put_totals = put_native_pages(
        endpoint=daemon.endpoint,
        page_files=page_files,
        xfer_bin=binaries["xfer"],
    )
    assert put_totals.success_count == len(page_files)
    inspect_native_pages(
        endpoint=daemon.endpoint,
        object_ids=[page.object_id for page in page_files],
        store_bin=binaries["store"],
    )

    daemon.restart()

    fetched_pages, get_totals = get_native_pages(
        endpoint=daemon.endpoint,
        expected_pages=prepared.pages,
        page_files=page_files,
        work_dir=tmp_path / "manual",
        xfer_bin=binaries["xfer"],
    )
    completion = complete_store_roundtrip(prepared, fetched_pages)

    assert get_totals.success_count == len(page_files)
    assert completion["continuation_match"] is True
    assert completion["logit_max_abs_error"] <= 1e-6


def _assert_get_payloads_validate(work_dir: Path, page_count: int) -> None:
    for index in range(page_count):
        target = json.loads(
            (work_dir / f"page-{index:04d}" / "target.json").read_text(
                encoding="utf-8"
            )
        )
        metadata = json.loads(
            (work_dir / "get" / f"page-{index:04d}" / "meta.json").read_text(
                encoding="utf-8"
            )
        )
        payload = (work_dir / "get" / f"page-{index:04d}" / "payload.bin").read_bytes()
        assert validate_object(metadata, payload, target).status == "accepted"


def _find_binary(name: str) -> Path | None:
    found = shutil.which(name)
    if found:
        return Path(found)
    candidate = REPO_ROOT / "bifrostd" / "target" / "debug" / name
    if candidate.exists():
        return candidate
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
