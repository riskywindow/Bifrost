"""Process orchestration for optional Phase 6 serving experiments."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .baseline_matrix import BaselineMode
from .processes import (
    ManagedProcess,
    ProcessReadinessTimeout,
    http_ready_check,
    tcp_ready_check,
)

SCENARIOS = {
    "fake",
    "vllm-lmcache-bifrost",
    "vllm-only",
    "lmcache-local",
    BaselineMode.VLLM_ONLY.value,
    BaselineMode.VLLM_LMCACHE_LOCAL_CPU.value,
    BaselineMode.VLLM_LMCACHE_BIFROST.value,
}
REAL_VLLM_SCENARIOS = SCENARIOS - {"fake"}


class OrchestratorError(RuntimeError):
    """Base error for deterministic Phase 6 orchestration failures."""


class OrchestratorSafetyError(OrchestratorError):
    """Raised before startup when an unsafe scenario is requested."""


@dataclass(frozen=True, slots=True)
class OrchestratorConfig:
    scenario: str = "fake"
    output_dir: Path = Path("runs/phase6-serving/orchestrator")
    model: str | None = None
    bifrost_endpoint: str = "127.0.0.1:7420"
    vllm_port: int = 8000
    lmcache_port: int = 9000
    allow_real_vllm: bool = False
    allow_model_downloads: bool = False
    dry_run: bool = False
    readiness_timeout_seconds: float = 10.0
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OrchestratorResult:
    scenario: str
    output_dir: Path
    dry_run: bool
    started: bool
    processes: list[dict[str, Any]]
    manifest_path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "bifrost.phase6_orchestrator_result.v1",
            "scenario": self.scenario,
            "output_dir": str(self.output_dir),
            "dry_run": self.dry_run,
            "started": self.started,
            "processes": self.processes,
            "manifest_path": str(self.manifest_path),
        }


def run_orchestration(config: OrchestratorConfig) -> OrchestratorResult:
    _validate_config(config)
    processes = build_processes(config)
    config.output_dir.mkdir(parents=True, exist_ok=True)

    if config.dry_run:
        result = OrchestratorResult(
            scenario=config.scenario,
            output_dir=config.output_dir,
            dry_run=True,
            started=False,
            processes=[process.status() for process in processes],
            manifest_path=config.output_dir / "orchestrator_manifest.json",
        )
        _write_manifest(result.manifest_path, result.to_dict())
        return result

    started: list[ManagedProcess] = []
    try:
        for process in processes:
            process.start()
            started.append(process)
            process.wait_ready(config.readiness_timeout_seconds)
        result = OrchestratorResult(
            scenario=config.scenario,
            output_dir=config.output_dir,
            dry_run=False,
            started=True,
            processes=[process.status() for process in processes],
            manifest_path=config.output_dir / "orchestrator_manifest.json",
        )
        _write_manifest(result.manifest_path, result.to_dict())
        return result
    except Exception:
        for process in reversed(started):
            process.stop()
        raise
    finally:
        if started:
            for process in reversed(started):
                process.stop()
            _write_manifest(
                config.output_dir / "orchestrator_final_status.json",
                {
                    "schema_version": "bifrost.phase6_orchestrator_final_status.v1",
                    "scenario": config.scenario,
                    "processes": [process.status() for process in processes],
                },
            )


def build_processes(config: OrchestratorConfig) -> list[ManagedProcess]:
    _validate_config(config)
    repo_root = Path(__file__).resolve().parents[2]
    cwd = config.cwd or repo_root
    if config.scenario == "fake":
        return [_fake_openai_server(config, cwd)]
    scenario = _normalized_scenario(config.scenario)
    if scenario == BaselineMode.VLLM_ONLY.value:
        return [_vllm_server(config, cwd, role="vllm_server", lmcache_mode=None)]
    if scenario == BaselineMode.VLLM_LMCACHE_LOCAL_CPU.value:
        return [
            _vllm_server(
                config,
                cwd,
                role="vllm_server",
                lmcache_mode="local",
            )
        ]
    if scenario == BaselineMode.VLLM_LMCACHE_BIFROST.value:
        return [
            _bifrost_daemon(config, cwd),
            _lmcache_server(config, cwd),
            _vllm_server(
                config,
                cwd,
                role="vllm_server",
                lmcache_mode="bifrost",
            ),
        ]
    raise OrchestratorError(f"unsupported scenario: {config.scenario}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Orchestrate Phase 6 serving processes"
    )
    parser.add_argument(
        "--scenario",
        choices=sorted(SCENARIOS),
        required=True,
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model", default=None)
    parser.add_argument("--bifrost-endpoint", default="127.0.0.1:7420")
    parser.add_argument("--vllm-port", type=int, default=8000)
    parser.add_argument("--lmcache-port", type=int, default=9000)
    parser.add_argument("--allow-real-vllm", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
        config = OrchestratorConfig(
            scenario=args.scenario,
            output_dir=args.output_dir,
            model=args.model,
            bifrost_endpoint=args.bifrost_endpoint,
            vllm_port=args.vllm_port,
            lmcache_port=args.lmcache_port,
            allow_real_vllm=args.allow_real_vllm,
            allow_model_downloads=os.environ.get("BIFROST_ALLOW_MODEL_DOWNLOADS")
            == "1",
            dry_run=args.dry_run,
        )
        result = run_orchestration(config)
        if args.json:
            print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        else:
            verb = "planned" if result.dry_run else "started and stopped"
            print(
                f"{verb} {len(result.processes)} process(es); wrote {result.manifest_path}"
            )
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        if "--json" in (argv or sys.argv[1:]):
            print(
                json.dumps({"status": "error", "error": str(exc)}, indent=2),
                file=sys.stderr,
            )
        else:
            print(f"bifrost serving orchestrator failed: {exc}", file=sys.stderr)
        return 2


def _validate_config(config: OrchestratorConfig) -> None:
    if config.scenario not in SCENARIOS:
        raise OrchestratorError(f"unsupported scenario: {config.scenario}")
    if config.vllm_port <= 0 or config.vllm_port > 65535:
        raise OrchestratorError("vllm-port must be in 1..65535")
    if config.lmcache_port <= 0 or config.lmcache_port > 65535:
        raise OrchestratorError("lmcache-port must be in 1..65535")
    _split_endpoint(config.bifrost_endpoint)
    if config.scenario in REAL_VLLM_SCENARIOS:
        # Planning only constructs command descriptions and writes a manifest. It is
        # intentionally safe in CI and must not be blocked by real-run guards.
        if config.dry_run:
            return
        if not (
            config.allow_real_vllm or os.environ.get("BIFROST_RUN_REAL_VLLM") == "1"
        ):
            raise OrchestratorSafetyError(
                "refusing to start real vLLM without --allow-real-vllm or BIFROST_RUN_REAL_VLLM=1"
            )
        if not config.model:
            raise OrchestratorSafetyError("real vLLM scenarios require --model")
        if not _is_local_path(config.model) and not config.allow_model_downloads:
            raise OrchestratorSafetyError(
                "refusing non-local model value without BIFROST_ALLOW_MODEL_DOWNLOADS=1"
            )
        if _running_in_ci():
            raise OrchestratorSafetyError("refusing real serving orchestration in CI")


def _fake_openai_server(config: OrchestratorConfig, cwd: Path) -> ManagedProcess:
    port = str(config.vllm_port)
    command = [
        sys.executable,
        "-u",
        str(cwd / "tools" / "bifrost_fake_openai_server.py"),
        "--host",
        "127.0.0.1",
        "--port",
        port,
        "--simulate-cache",
        "true",
        "--base-delay-ms",
        "1",
        "--cache-hit-delay-ms",
        "0",
    ]
    return ManagedProcess(
        name="fake_openai_server",
        command=command,
        env=_child_env(config, role="fake_openai_server"),
        cwd=cwd,
        log_path=config.output_dir / "fake_openai_server.log",
        ready_check=http_ready_check(f"http://127.0.0.1:{port}/healthz"),
    )


def _bifrost_daemon(config: OrchestratorConfig, cwd: Path) -> ManagedProcess:
    daemon = _bifrost_daemon_command(cwd)
    host, port = _split_endpoint(config.bifrost_endpoint)
    spool = config.output_dir / "bifrost_spool"
    trace = config.output_dir / "bifrost_daemon_trace.jsonl"
    command = [
        daemon,
        "--listen",
        config.bifrost_endpoint,
        "--spool",
        str(spool),
        "--trace-jsonl",
        str(trace),
    ]
    return ManagedProcess(
        name="bifrost_daemon",
        command=command,
        env=_child_env(config, role="bifrost_daemon"),
        cwd=cwd,
        log_path=config.output_dir / "bifrost_daemon.log",
        ready_check=tcp_ready_check(host, port),
    )


def _lmcache_server(config: OrchestratorConfig, cwd: Path) -> ManagedProcess:
    command = [
        sys.executable,
        "-m",
        "lmcache.server",
        "--host",
        "127.0.0.1",
        "--port",
        str(config.lmcache_port),
    ]
    return ManagedProcess(
        name="lmcache_server",
        command=command,
        env=_child_env(config, role="lmcache_server"),
        cwd=cwd,
        log_path=config.output_dir / "lmcache_server.log",
        ready_check=tcp_ready_check("127.0.0.1", config.lmcache_port),
    )


def _vllm_server(
    config: OrchestratorConfig,
    cwd: Path,
    *,
    role: str,
    lmcache_mode: str | None,
) -> ManagedProcess:
    assert config.model is not None
    command = [
        "vllm",
        "serve",
        config.model,
        "--host",
        "127.0.0.1",
        "--port",
        str(config.vllm_port),
        "--no-enable-prefix-caching",
    ]
    return ManagedProcess(
        name=role,
        command=command,
        env=_child_env(config, role=role, lmcache_mode=lmcache_mode),
        cwd=cwd,
        log_path=config.output_dir / f"{role}.log",
        ready_check=http_ready_check(f"http://127.0.0.1:{config.vllm_port}/health"),
    )


def _child_env(
    config: OrchestratorConfig,
    *,
    role: str,
    lmcache_mode: str | None = None,
) -> dict[str, str]:
    env = dict(config.env)
    env.update(
        {
            "BIFROST_PHASE6_ROLE": role,
            "BIFROST_ENDPOINT": config.bifrost_endpoint,
            "BIFROST_VLLM_PORT": str(config.vllm_port),
            "BIFROST_LMCACHE_PORT": str(config.lmcache_port),
        }
    )
    if config.model is not None:
        env["BIFROST_VLLM_MODEL"] = config.model
    if lmcache_mode == "bifrost":
        env["LMCACHE_CONFIG_FILE"] = str(
            config.output_dir
            / BaselineMode.VLLM_LMCACHE_BIFROST.value
            / "lmcache_config.yaml"
        )
    elif lmcache_mode == "local":
        env["LMCACHE_CONFIG_FILE"] = str(
            config.output_dir
            / BaselineMode.VLLM_LMCACHE_LOCAL_CPU.value
            / "lmcache_config.yaml"
        )
        env["BIFROST_LMCACHE_MODE"] = "local_cpu"
    return env


def _normalized_scenario(scenario: str) -> str:
    aliases = {
        "vllm-only": BaselineMode.VLLM_ONLY.value,
        "lmcache-local": BaselineMode.VLLM_LMCACHE_LOCAL_CPU.value,
        "vllm-lmcache-bifrost": BaselineMode.VLLM_LMCACHE_BIFROST.value,
    }
    return aliases.get(scenario, scenario)


def _write_manifest(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _split_endpoint(endpoint: str) -> tuple[str, int]:
    if ":" not in endpoint:
        raise OrchestratorError("bifrost-endpoint must be HOST:PORT")
    host, port_text = endpoint.rsplit(":", 1)
    if not host:
        raise OrchestratorError("bifrost-endpoint host must be non-empty")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise OrchestratorError("bifrost-endpoint port must be an integer") from exc
    if port <= 0 or port > 65535:
        raise OrchestratorError("bifrost-endpoint port must be in 1..65535")
    return host, port


def _running_in_ci() -> bool:
    return os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"


def _is_local_path(model: str) -> bool:
    path = Path(model).expanduser()
    return path.exists()


def _bifrost_daemon_command(repo_root: Path) -> str:
    candidate = repo_root / "bifrostd" / "target" / "debug" / "bifrost-daemon"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("bifrost-daemon")
    if found:
        return found
    return str(candidate)


__all__ = [
    "OrchestratorConfig",
    "OrchestratorError",
    "OrchestratorResult",
    "OrchestratorSafetyError",
    "ProcessReadinessTimeout",
    "build_processes",
    "main",
    "run_orchestration",
]
