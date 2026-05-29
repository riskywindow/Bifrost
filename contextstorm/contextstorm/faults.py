"""Optional local fault profiles for ContextStorm."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROFILE_ROOT = Path(__file__).resolve().parents[1] / "network_profiles"
VALID_FAULT_TYPES = {"none", "tc_netem", "process_kill", "artificial_delay"}


class FaultProfileError(ValueError):
    pass


@dataclass(frozen=True)
class FaultProfile:
    type: str = "none"
    interface: str | None = None
    delay_ms: int | None = None
    jitter_ms: int | None = None
    loss_percent: float | None = None
    rate_mbit: int | None = None
    apply_at_ms: int = 0
    remove_at_ms: int | None = None
    target_path: str | None = None
    path: Path | None = None


class FaultController:
    """Run-scoped scheduler for best-effort local fault injection."""

    def __init__(
        self,
        profile: FaultProfile,
        *,
        allow_root_faults: bool,
        events_path: Path,
    ) -> None:
        self.profile = profile
        self.allow_root_faults = allow_root_faults
        self.events_path = events_path
        self._daemons: dict[str, subprocess.Popen[str]] = {}
        self._threads: list[threading.Thread] = []
        self._started_at = time.monotonic()
        self._cleanup_commands: list[list[str]] = []
        self._artificial_delay_applied = False
        self._lock = threading.Lock()
        self.events_path.parent.mkdir(parents=True, exist_ok=True)

    def register_daemons(self, daemons: list[dict[str, Any]]) -> None:
        self._daemons = {
            str(daemon["path_name"]): daemon["process"] for daemon in daemons
        }

    def start(self) -> None:
        self.record_event("fault_profile_loaded", status="ok")
        if self.profile.type == "none":
            self.record_event(
                "fault_skipped", status="skipped", reason="fault_profile_none"
            )
            return
        thread = threading.Thread(target=self._run_apply, name="contextstorm-fault")
        thread.daemon = True
        thread.start()
        self._threads.append(thread)

        if self.profile.remove_at_ms is not None and self.profile.type == "tc_netem":
            cleanup_thread = threading.Thread(
                target=self._run_scheduled_cleanup,
                name="contextstorm-fault-cleanup",
            )
            cleanup_thread.daemon = True
            cleanup_thread.start()
            self._threads.append(cleanup_thread)

    def maybe_apply_artificial_delay(self, target_path: str | None = None) -> None:
        profile = self.profile
        if profile.type != "artificial_delay" or self._artificial_delay_applied:
            return
        if profile.target_path and target_path and profile.target_path != target_path:
            return
        self._sleep_until_ms(profile.apply_at_ms)
        delay_ms = max(0, int(profile.delay_ms or 0))
        self.record_event("fault_apply_begin", status="ok")
        if delay_ms:
            time.sleep(delay_ms / 1000)
        self._artificial_delay_applied = True
        self.record_event(
            "fault_apply_complete",
            status="ok",
            reason=f"artificial_delay_{delay_ms}ms",
        )

    def cleanup(self) -> None:
        if self.profile.type == "tc_netem":
            self._cleanup_tc()
        for thread in self._threads:
            thread.join(timeout=1)
        self.record_event("fault_cleanup_complete", status="ok")

    def cleanup_commands(self) -> list[list[str]]:
        if self.profile.type != "tc_netem":
            return []
        return [_tc_cleanup_command(self.profile)]

    def record_event(
        self,
        event_type: str,
        *,
        status: str,
        reason: str | None = None,
        commands: list[list[str]] | None = None,
    ) -> None:
        event = {
            "timestamp_unix_ms": int(time.time() * 1000),
            "event_type": event_type,
            "fault_type": self.profile.type,
            "target_path": self.profile.target_path,
            "status": status,
        }
        if reason:
            event["reason"] = reason
        if commands:
            event["commands"] = commands
        with self._lock:
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")

    def _run_apply(self) -> None:
        self._sleep_until_ms(self.profile.apply_at_ms)
        if self.profile.type == "tc_netem":
            self._apply_tc_netem()
        elif self.profile.type == "process_kill":
            self._apply_process_kill()
        elif self.profile.type == "artificial_delay":
            return
        else:
            self.record_event("fault_skipped", status="skipped", reason="unknown_fault_type")

    def _run_scheduled_cleanup(self) -> None:
        self._sleep_until_ms(int(self.profile.remove_at_ms or 0))
        self._cleanup_tc()

    def _apply_tc_netem(self) -> None:
        commands = [_tc_apply_command(self.profile)]
        cleanup_commands = [_tc_cleanup_command(self.profile)]
        self._cleanup_commands = cleanup_commands
        self.record_event("fault_apply_begin", status="pending", commands=commands)
        print("ContextStorm fault tc command:", _format_command(commands[0]), flush=True)
        print(
            "ContextStorm fault cleanup command:",
            _format_command(cleanup_commands[0]),
            flush=True,
        )
        if not self.allow_root_faults:
            self.record_event(
                "fault_skipped",
                status="skipped",
                reason="tc_netem_requires_--allow-root-faults",
                commands=commands,
            )
            return
        if os.geteuid() != 0:
            self.record_event(
                "fault_skipped",
                status="skipped",
                reason="tc_netem_requires_root_privileges",
                commands=commands,
            )
            return
        if shutil.which("tc") is None:
            self.record_event(
                "fault_skipped",
                status="skipped",
                reason="tc_command_unavailable",
                commands=commands,
            )
            return
        completed = subprocess.run(
            commands[0],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        status = "ok" if completed.returncode == 0 else "failed"
        self.record_event(
            "fault_apply_complete",
            status=status,
            reason=(completed.stderr or completed.stdout).strip() or None,
            commands=commands,
        )

    def _cleanup_tc(self) -> None:
        if not self._cleanup_commands:
            self._cleanup_commands = self.cleanup_commands()
        for command in self._cleanup_commands:
            print("ContextStorm fault cleanup command:", _format_command(command), flush=True)
            if not self.allow_root_faults or os.geteuid() != 0 or shutil.which("tc") is None:
                self.record_event(
                    "fault_cleanup_skipped",
                    status="skipped",
                    reason="tc_cleanup_not_permitted_or_unavailable",
                    commands=[command],
                )
                continue
            completed = subprocess.run(
                command,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            status = "ok" if completed.returncode == 0 else "failed"
            self.record_event(
                "fault_cleanup",
                status=status,
                reason=(completed.stderr or completed.stdout).strip() or None,
                commands=[command],
            )

    def _apply_process_kill(self) -> None:
        target = self.profile.target_path
        if not target:
            self.record_event(
                "fault_skipped",
                status="skipped",
                reason="process_kill_requires_target_path",
            )
            return
        process = self._daemons.get(target)
        if process is None:
            self.record_event(
                "fault_skipped",
                status="skipped",
                reason=f"target_path_not_running:{target}",
            )
            return
        if process.poll() is not None:
            self.record_event(
                "fault_skipped",
                status="skipped",
                reason=f"target_path_already_exited:{target}",
            )
            return
        self.record_event("fault_apply_begin", status="ok")
        process.kill()
        self.record_event("fault_apply_complete", status="ok", reason="process_killed")

    def _sleep_until_ms(self, relative_ms: int) -> None:
        deadline = self._started_at + max(0, relative_ms) / 1000
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)


def load_fault_profile(profile: str | None) -> FaultProfile:
    if not profile:
        return FaultProfile()
    path = resolve_fault_profile(profile)
    if path is None:
        return FaultProfile(type="none", path=None)
    data = _load_simple_yaml(path)
    fault_type = str(data.get("type", "none"))
    if fault_type not in VALID_FAULT_TYPES:
        raise FaultProfileError(f"unsupported fault profile type in {path}: {fault_type}")
    return FaultProfile(
        type=fault_type,
        interface=_optional_str(data.get("interface")),
        delay_ms=_optional_int(data.get("delay_ms")),
        jitter_ms=_optional_int(data.get("jitter_ms")),
        loss_percent=_optional_float(data.get("loss_percent")),
        rate_mbit=_optional_int(data.get("rate_mbit")),
        apply_at_ms=int(data.get("apply_at_ms") or 0),
        remove_at_ms=_optional_int(data.get("remove_at_ms")),
        target_path=_optional_str(data.get("target_path")),
        path=path,
    )


def resolve_fault_profile(profile: str) -> Path | None:
    candidate = Path(profile)
    candidates = []
    if candidate.suffix:
        candidates.append(candidate)
    else:
        candidates.append(PROFILE_ROOT / f"{profile}.yaml")
        candidates.append(PROFILE_ROOT / profile)
    candidates.append(PROFILE_ROOT / candidate.name)
    for item in candidates:
        if item.exists():
            return item
    return None


def tc_apply_command(profile: FaultProfile) -> list[str]:
    return _tc_apply_command(profile)


def tc_cleanup_command(profile: FaultProfile) -> list[str]:
    return _tc_cleanup_command(profile)


def _tc_apply_command(profile: FaultProfile) -> list[str]:
    if not profile.interface:
        raise FaultProfileError("tc_netem fault requires interface")
    command = ["tc", "qdisc", "replace", "dev", profile.interface, "root", "netem"]
    if profile.delay_ms is not None:
        command.extend(["delay", f"{profile.delay_ms}ms"])
        if profile.jitter_ms is not None:
            command.append(f"{profile.jitter_ms}ms")
    if profile.loss_percent is not None:
        command.extend(["loss", f"{profile.loss_percent:g}%"])
    if profile.rate_mbit is not None:
        command.extend(["rate", f"{profile.rate_mbit}mbit"])
    return command


def _tc_cleanup_command(profile: FaultProfile) -> list[str]:
    if not profile.interface:
        raise FaultProfileError("tc_netem fault requires interface")
    return ["tc", "qdisc", "del", "dev", profile.interface, "root"]


def _format_command(command: list[str]) -> str:
    return " ".join(command)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _load_simple_yaml(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise FaultProfileError(f"expected key/value in {path}: {raw_line}")
        key, value = line.split(":", 1)
        result[key.strip()] = _parse_scalar(value.strip())
    return result


def _parse_scalar(value: str) -> Any:
    if value == "":
        return None
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None"}:
        return None
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value
