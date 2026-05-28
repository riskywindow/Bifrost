from __future__ import annotations

import socket
from pathlib import Path

import pytest

from contextstorm.runner import load_scenario, run_scenario


def test_scenario_yaml_loads() -> None:
    scenario = load_scenario(Path("scenarios/small_ci.yaml"))

    assert scenario.name == "small_ci"
    assert scenario.object_size_bytes == 1048576
    assert scenario.operations == ("put", "has", "get")
    assert scenario.paths[0].name == "primary"


def test_small_ci_runs_when_binaries_are_available(tmp_path: Path) -> None:
    daemon = Path("../bifrostd/target/debug/bifrost-daemon")
    xfer = Path("../bifrostd/target/debug/bifrost-xfer")
    if not daemon.exists() or not xfer.exists():
        pytest.skip("bifrost Rust binaries are not built")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
    except OSError as exc:
        pytest.skip(f"loopback bind is unavailable in this environment: {exc}")

    run_dir = run_scenario(
        Path("scenarios/small_ci.yaml"),
        runs_root=tmp_path,
        run_id="small-ci-test",
    )

    assert (run_dir / "run.json").exists()
