from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from contextstorm.faults import (
    FaultController,
    FaultProfile,
    load_fault_profile,
    tc_apply_command,
    tc_cleanup_command,
)
from contextstorm.runner import run_scenario


def read_events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_fault_profile_yaml_loads() -> None:
    clean = load_fault_profile("clean")
    delay = load_fault_profile("delay_50ms")
    path_death = load_fault_profile("path_death")

    assert clean.type == "none"
    assert delay.type == "tc_netem"
    assert delay.interface == "lo"
    assert delay.delay_ms == 50
    assert path_death.type == "process_kill"
    assert path_death.target_path == "secondary"


def test_tc_netem_profile_skips_without_root_opt_in(tmp_path: Path) -> None:
    events_path = tmp_path / "fault_events.jsonl"
    controller = FaultController(
        load_fault_profile("loss_1pct"),
        allow_root_faults=False,
        events_path=events_path,
    )

    controller.start()
    controller.cleanup()

    events = read_events(events_path)
    assert any(
        event["event_type"] == "fault_skipped"
        and event["reason"] == "tc_netem_requires_--allow-root-faults"
        for event in events
    )


def test_tc_cleanup_command_generation() -> None:
    profile = FaultProfile(
        type="tc_netem",
        interface="lo",
        delay_ms=50,
        jitter_ms=5,
        loss_percent=1.0,
        rate_mbit=50,
    )

    assert tc_apply_command(profile) == [
        "tc",
        "qdisc",
        "replace",
        "dev",
        "lo",
        "root",
        "netem",
        "delay",
        "50ms",
        "5ms",
        "loss",
        "1%",
        "rate",
        "50mbit",
    ]
    assert tc_cleanup_command(profile) == ["tc", "qdisc", "del", "dev", "lo", "root"]


def test_fault_events_are_written_to_run_artifact(tmp_path: Path) -> None:
    events_path = tmp_path / "fault_events.jsonl"
    controller = FaultController(
        FaultProfile(type="artificial_delay", delay_ms=1, apply_at_ms=0),
        allow_root_faults=False,
        events_path=events_path,
    )

    controller.start()
    controller.maybe_apply_artificial_delay("primary")
    controller.cleanup()

    events = read_events(events_path)
    event_types = [event["event_type"] for event in events]
    assert "fault_profile_loaded" in event_types
    assert "fault_apply_complete" in event_types
    assert "fault_cleanup_complete" in event_types


def test_process_kill_fault_can_run_in_small_local_scenario(tmp_path: Path) -> None:
    daemon = Path("../bifrostd/target/debug/bifrost-daemon")
    xfer = Path("../bifrostd/target/debug/bifrost-xfer")
    if not daemon.exists() or not xfer.exists():
        pytest.skip("bifrost Rust binaries are not built")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
    except OSError as exc:
        pytest.skip(f"loopback bind is unavailable in this environment: {exc}")

    profile = tmp_path / "kill_secondary.yaml"
    profile.write_text(
        "\n".join(
            [
                "type: process_kill",
                "target_path: secondary",
                "apply_at_ms: 0",
                "",
            ]
        )
    )
    scenario = tmp_path / "process_kill.yaml"
    scenario.write_text(
        "\n".join(
            [
                "name: process_kill_test",
                "object_size_bytes: 1048576",
                "chunk_size_bytes: 262144",
                "object_type: opaque_engine_blob",
                "paths:",
                "  - name: primary",
                "    start_daemon: true",
                "  - name: secondary",
                "    start_daemon: true",
                "operations: [put, has, get]",
                "repetitions: 1",
                f"fault_profile: {profile}",
                "timeout_seconds: 30",
                "",
            ]
        )
    )

    run_dir = run_scenario(scenario, runs_root=tmp_path, run_id="process-kill-test")
    events = read_events(run_dir / "fault_events.jsonl")

    assert any(
        event["event_type"] == "fault_apply_complete"
        and event.get("reason") == "process_killed"
        for event in events
    )
