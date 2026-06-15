"""Local process lifecycle helpers for Phase 6 serving experiments."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ReadyCheck = Callable[[], bool]


class ProcessError(RuntimeError):
    """Base error for deterministic process orchestration failures."""


class ProcessStartError(ProcessError):
    """Raised when a managed process cannot be started."""


class ProcessReadinessTimeout(ProcessError):
    """Raised when a process does not become ready in time."""


@dataclass(slots=True)
class ManagedProcess:
    name: str
    command: Sequence[str]
    env: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None
    log_path: Path | None = None
    ready_check: ReadyCheck | None = None
    process: subprocess.Popen[bytes] | None = field(default=None, init=False, repr=False)
    _log_handle: Any | None = field(default=None, init=False, repr=False)
    started_at: float | None = field(default=None, init=False)
    stopped_at: float | None = field(default=None, init=False)

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            raise ProcessStartError(f"{self.name} is already running")
        if not self.command:
            raise ProcessStartError(f"{self.name} command must be non-empty")

        log_path = self.log_path or Path(f"{self.name}.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = log_path.open("ab")
        child_env = os.environ.copy()
        child_env.update(self.env)
        try:
            self.process = subprocess.Popen(
                list(self.command),
                cwd=str(self.cwd) if self.cwd is not None else None,
                env=child_env,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception as exc:
            self._close_log()
            raise ProcessStartError(f"failed to start {self.name}: {exc}") from exc
        self.started_at = time.time()
        self.stopped_at = None

    def wait_ready(self, timeout: float) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.process is None:
            raise ProcessReadinessTimeout(f"{self.name} has not been started")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            returncode = self.process.poll()
            if returncode is not None:
                self._close_log()
                raise ProcessReadinessTimeout(
                    f"{self.name} exited before readiness with code {returncode}"
                )
            if self.ready_check is None:
                return
            try:
                if self.ready_check():
                    return
            except Exception:
                pass
            time.sleep(0.05)
        raise ProcessReadinessTimeout(f"{self.name} readiness timed out after {timeout}s")

    def stop(self, timeout: float = 5.0) -> None:
        if self.process is None:
            self._close_log()
            return
        if self.process.poll() is not None:
            self.stopped_at = time.time()
            self._close_log()
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            self.process.terminate()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill()
            return
        finally:
            self.stopped_at = time.time()
            self._close_log()

    def kill(self) -> None:
        if self.process is None:
            self._close_log()
            return
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                self.process.kill()
            self.process.wait(timeout=5)
        self.stopped_at = time.time()
        self._close_log()

    def status(self) -> dict[str, Any]:
        pid = self.process.pid if self.process is not None else None
        returncode = self.process.poll() if self.process is not None else None
        running = self.process is not None and returncode is None
        return {
            "name": self.name,
            "command": list(self.command),
            "cwd": str(self.cwd) if self.cwd is not None else None,
            "log_path": str(self.log_path) if self.log_path is not None else None,
            "pid": pid,
            "running": running,
            "returncode": returncode,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
        }

    def _close_log(self) -> None:
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None


def http_ready_check(url: str, *, timeout_seconds: float = 0.5) -> ReadyCheck:
    def check() -> bool:
        try:
            with urllib.request.urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310
                return 200 <= int(response.status) < 500
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    return check


def tcp_ready_check(host: str, port: int, *, timeout_seconds: float = 0.5) -> ReadyCheck:
    def check() -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout_seconds):
                return True
        except OSError:
            return False

    return check


__all__ = [
    "ManagedProcess",
    "ProcessError",
    "ProcessReadinessTimeout",
    "ProcessStartError",
    "ReadyCheck",
    "http_ready_check",
    "tcp_ready_check",
]
