"""Phase 6 environment readiness doctor.

The doctor is intentionally read-only: it imports packages, checks local
commands, probes explicitly configured local endpoints, and inspects local
paths. It must not start vLLM, download models, or require GPU hardware.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import importlib
import importlib.metadata
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

READY = "ready"
NOT_READY = "not_ready"
SKIPPED = "skipped"
UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: str
    details: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    fix: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "status": self.status,
            "details": self.details,
        }
        if self.reason:
            data["reason"] = self.reason
        if self.fix:
            data["recommended_fix"] = self.fix
        return data


@dataclass(frozen=True, slots=True)
class ReadinessLevel:
    status: str
    reasons: list[str] = field(default_factory=list)
    recommended_fixes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reasons": self.reasons,
            "recommended_fixes": self.recommended_fixes,
        }


@dataclass(frozen=True, slots=True)
class EnvDoctorConfig:
    endpoint: str = "127.0.0.1:7420"
    model: str | None = None
    output_dir: Path = Path("runs/phase6-env-doctor")
    min_free_disk_bytes: int = 256 * 1024 * 1024
    required_ports: tuple[int, ...] = (8000, 8001, 8002)
    daemon_timeout_seconds: float = 1.0
    verbose: bool = False


@dataclass(frozen=True, slots=True)
class EnvDoctorReport:
    checks: dict[str, CheckResult]
    readiness: dict[str, ReadinessLevel]

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": {name: check.to_dict() for name, check in self.checks.items()},
            "readiness": {
                name: level.to_dict() for name, level in self.readiness.items()
            },
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


def run_doctor(config: EnvDoctorConfig | None = None) -> EnvDoctorReport:
    config = config or EnvDoctorConfig()
    _ensure_repo_source_paths()
    checks: dict[str, CheckResult] = {}

    checks["python"] = _check_python()
    checks["platform"] = _check_platform()
    checks["git"] = _check_git()
    checks["output_directory"] = _check_output_directory(
        config.output_dir,
        config.min_free_disk_bytes,
    )
    checks["torch"] = _check_torch()
    checks["vllm"] = _check_import("vllm", distributions=("vllm",))
    checks["lmcache"] = _check_import(
        "lmcache",
        distributions=("lmcache", "lmcache-vllm"),
    )
    checks["bifrost_client"] = _check_import(
        "bifrost_client",
        distributions=("bifrost-py",),
        required_attrs=("BifrostClient", "BifrostClientConfig"),
    )
    checks["lmcache_bifrost"] = _check_import(
        "lmcache_bifrost",
        distributions=("lmcache-bifrost",),
        required_attrs=("BifrostLMCacheConfig",),
    )
    checks["lmcache_bifrost_adapter"] = _check_import(
        "lmcache_bifrost.adapter",
        distributions=("lmcache-bifrost",),
        required_attrs=("BifrostRemoteConnector",),
    )
    checks["lmcache_bifrost_config"] = _check_connector_config(config.endpoint)
    checks["bifrostd_binary"] = _check_binary("bifrost-daemon")
    checks["bifrost_daemon"] = _check_bifrost_daemon(
        config.endpoint,
        config.daemon_timeout_seconds,
    )
    checks["disk_space"] = _check_disk_space(
        config.output_dir,
        config.min_free_disk_bytes,
    )
    checks["vllm_cli"] = _check_cli("vllm")
    checks["vllm_kv_transfer"] = _check_vllm_kv_transfer(checks["vllm_cli"])
    checks["vllm_bench_serve"] = _check_vllm_bench_serve(checks["vllm_cli"])
    checks["huggingface_token"] = _check_huggingface_token()
    checks["model"] = _check_model(config.model)
    checks["ports"] = _check_ports(config.required_ports)

    readiness = _readiness(checks)
    return EnvDoctorReport(checks=checks, readiness=readiness)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BIFROST Phase 6 environment doctor")
    parser.add_argument("--endpoint", default="127.0.0.1:7420", help="BIFROST daemon HOST:PORT")
    parser.add_argument("--model", default=None, help="Optional local model path or model ID")
    parser.add_argument("--output-json", default=None, help="Write JSON report to this path")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--verbose", action="store_true", help="Include detailed text output")
    try:
        args = parser.parse_args(argv)
        config = EnvDoctorConfig(
            endpoint=args.endpoint,
            model=args.model,
            verbose=args.verbose,
        )
        report = run_doctor(config)
        if args.output_json:
            output_path = Path(args.output_json)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(report.to_json(indent=2) + "\n", encoding="utf-8")
        if args.json:
            print(report.to_json(indent=2))
        else:
            print(format_text_report(report, verbose=args.verbose))
        return 0 if report.readiness["fake_ci_ready"].status == READY else 1
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"bifrost env doctor failed: {exc}", file=sys.stderr)
        return 2


def format_text_report(report: EnvDoctorReport, *, verbose: bool = False) -> str:
    lines = ["BIFROST Phase 6 environment doctor", "", "Readiness:"]
    for name, level in report.readiness.items():
        lines.append(f"- {name}: {level.status}")
        if verbose:
            for reason in level.reasons:
                lines.append(f"  reason: {reason}")
            for fix in level.recommended_fixes:
                lines.append(f"  fix: {fix}")
    if verbose:
        lines.extend(["", "Checks:"])
        for name, check in report.checks.items():
            suffix = f" - {check.reason}" if check.reason else ""
            lines.append(f"- {name}: {check.status}{suffix}")
    return "\n".join(lines)


def _ensure_repo_source_paths() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    for path in (repo_root / "bifrost_py", repo_root / "integrations" / "lmcache_bifrost"):
        text = str(path)
        if path.exists() and text not in sys.path:
            sys.path.insert(0, text)


def _check_python() -> CheckResult:
    version = sys.version_info
    details = {
        "version": platform.python_version(),
        "executable": sys.executable,
        "implementation": platform.python_implementation(),
    }
    if version >= (3, 11):
        return CheckResult("python", READY, details)
    return CheckResult(
        "python",
        NOT_READY,
        details,
        reason="Python 3.11 or newer is required for Phase 6 tools.",
        fix="Use the repository Python 3.11+ environment.",
    )


def _check_platform() -> CheckResult:
    details: dict[str, Any] = {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }
    memory = _memory_bytes()
    if memory is not None:
        details["memory_bytes"] = memory
    return CheckResult("platform", READY, details)


def _memory_bytes() -> int | None:
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages) * int(page_size)
    except (AttributeError, OSError, ValueError):
        return None


def _check_git() -> CheckResult:
    root = _run_git(["rev-parse", "--show-toplevel"])
    commit = _run_git(["rev-parse", "HEAD"])
    dirty = _run_git(["status", "--porcelain"])
    if root.returncode != 0 or commit.returncode != 0:
        return CheckResult(
            "git",
            UNKNOWN,
            {"error": root.stderr or commit.stderr},
            reason="Git metadata is unavailable.",
            fix="Run the doctor from a Git checkout to include commit metadata.",
        )
    return CheckResult(
        "git",
        READY,
        {
            "repository": root.stdout.strip(),
            "commit": commit.stdout.strip(),
            "dirty": bool(dirty.stdout.strip()),
        },
    )


def _run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False,
    )


def _check_output_directory(path: Path, min_free_bytes: int) -> CheckResult:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".bifrost_env_doctor_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return CheckResult(
            "output_directory",
            NOT_READY,
            {"path": str(path), "min_free_disk_bytes": min_free_bytes},
            reason=f"Output directory is not writable: {exc}",
            fix="Choose a writable benchmark output directory.",
        )
    return CheckResult(
        "output_directory",
        READY,
        {"path": str(path), "min_free_disk_bytes": min_free_bytes},
    )


def _check_disk_space(path: Path, min_free_bytes: int) -> CheckResult:
    try:
        path.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return CheckResult(
            "disk_space",
            NOT_READY,
            {"path": str(path), "min_free_disk_bytes": min_free_bytes},
            reason=f"Disk space could not be inspected: {exc}",
            fix="Choose an existing writable output directory.",
        )
    details = {
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "min_free_disk_bytes": min_free_bytes,
    }
    if usage.free >= min_free_bytes:
        return CheckResult("disk_space", READY, details)
    return CheckResult(
        "disk_space",
        NOT_READY,
        details,
        reason="Free disk space is below the configured benchmark threshold.",
        fix="Free disk space or choose another output directory.",
    )


def _check_torch() -> CheckResult:
    try:
        torch = importlib.import_module("torch")
    except Exception as exc:
        return CheckResult(
            "torch",
            NOT_READY,
            {"error": repr(exc)},
            reason="torch is not importable.",
            fix="Install torch in the environment for vLLM import and GPU readiness checks.",
        )
    details: dict[str, Any] = {
        "version": str(getattr(torch, "__version__", "")),
        "module_path": str(getattr(torch, "__file__", "")),
    }
    try:
        cuda_available = bool(torch.cuda.is_available())
        device_count = int(torch.cuda.device_count()) if cuda_available else 0
        details["cuda_available"] = cuda_available
        details["cuda_device_count"] = device_count
        details["cuda_version"] = getattr(getattr(torch, "version", None), "cuda", None)
        details["gpu_names"] = [
            str(torch.cuda.get_device_name(index)) for index in range(device_count)
        ]
    except Exception as exc:
        details["cuda_error"] = repr(exc)
        details["cuda_available"] = False
        details["cuda_device_count"] = 0
        details["gpu_names"] = []
    return CheckResult("torch", READY, details)


def _check_import(
    module_name: str,
    *,
    distributions: tuple[str, ...],
    required_attrs: tuple[str, ...] = (),
) -> CheckResult:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            module = importlib.import_module(module_name)
    except Exception as exc:
        return CheckResult(
            module_name,
            NOT_READY,
            {
                "module": module_name,
                "error": repr(exc),
                "import_stdout": stdout.getvalue()[-1000:],
                "import_stderr": stderr.getvalue()[-1000:],
            },
            reason=f"{module_name} is not importable.",
            fix=f"Install or expose {module_name} on PYTHONPATH when this optional level is needed.",
        )
    missing_attrs = [attr for attr in required_attrs if not hasattr(module, attr)]
    details = {
        "module": module_name,
        "module_path": str(getattr(module, "__file__", "")),
        "version": _module_version(module, distributions),
    }
    if stdout.getvalue():
        details["import_stdout"] = stdout.getvalue()[-1000:]
    if stderr.getvalue():
        details["import_stderr"] = stderr.getvalue()[-1000:]
    if missing_attrs:
        details["missing_attrs"] = missing_attrs
        return CheckResult(
            module_name,
            NOT_READY,
            details,
            reason=f"{module_name} imported but required attributes are missing.",
            fix=f"Use a compatible {module_name} package.",
        )
    return CheckResult(module_name, READY, details)


def _module_version(module: Any, distributions: tuple[str, ...]) -> str | None:
    version = getattr(module, "__version__", None)
    if version:
        return str(version)
    for distribution in distributions:
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def _check_connector_config(endpoint: str) -> CheckResult:
    try:
        from lmcache_bifrost import BifrostLMCacheConfig

        config = BifrostLMCacheConfig(endpoint=endpoint)
    except Exception as exc:
        return CheckResult(
            "lmcache_bifrost_config",
            NOT_READY,
            {"endpoint": endpoint, "error": repr(exc)},
            reason="LMCache BIFROST connector configuration is invalid.",
            fix="Use a non-empty HOST:PORT endpoint and a compatible connector package.",
        )
    return CheckResult(
        "lmcache_bifrost_config",
        READY,
        {
            "endpoint": config.endpoint,
            "chunk_size": config.chunk_size,
            "engine_name": config.engine_name,
            "integration_name": config.integration_name,
            "strict_validation": config.strict_validation,
        },
    )


def _check_binary(name: str) -> CheckResult:
    found = shutil.which(name)
    repo_candidate = Path(__file__).resolve().parents[2] / "bifrostd" / "target" / "debug" / name
    if found:
        return CheckResult(name, READY, {"path": found})
    if repo_candidate.exists():
        return CheckResult(name, READY, {"path": str(repo_candidate)})
    return CheckResult(
        name,
        NOT_READY,
        {"searched": [name, str(repo_candidate)]},
        reason=f"{name} binary was not found.",
        fix="Build bifrostd with `cargo build --manifest-path bifrostd/Cargo.toml --bins`.",
    )


def _check_bifrost_daemon(endpoint: str, timeout_seconds: float) -> CheckResult:
    try:
        from bifrost_client import BifrostClient, BifrostClientConfig

        client = BifrostClient(
            config=BifrostClientConfig(
                endpoint=endpoint,
                timeout_seconds=timeout_seconds,
            )
        )
        try:
            reachable = client.ping()
            stats = dataclasses.asdict(client.stats()) if reachable else None
        finally:
            client.close()
    except Exception as exc:
        return CheckResult(
            "bifrost_daemon",
            NOT_READY,
            {"endpoint": endpoint, "error": repr(exc)},
            reason="BIFROST daemon is not reachable at the configured endpoint.",
            fix="Start bifrost-daemon for LMCache connector and full benchmark readiness.",
        )
    return CheckResult(
        "bifrost_daemon",
        READY,
        {"endpoint": endpoint, "reachable": True, "store_stats": stats},
    )


def _check_cli(name: str) -> CheckResult:
    path = shutil.which(name)
    if path:
        return CheckResult(name, READY, {"path": path})
    return CheckResult(
        name,
        NOT_READY,
        {},
        reason=f"{name} CLI is not on PATH.",
        fix=f"Install {name} or activate the environment that provides it.",
    )


def _check_vllm_bench_serve(vllm_cli: CheckResult) -> CheckResult:
    path = vllm_cli.details.get("path")
    if vllm_cli.status != READY or not path:
        return CheckResult(
            "vllm_bench_serve",
            NOT_READY,
            {},
            reason="vLLM CLI is unavailable, so `vllm bench serve` cannot be checked.",
            fix="Install vLLM with a CLI that supports `vllm bench serve`.",
        )
    try:
        result = subprocess.run(
            [str(path), "bench", "serve", "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        return CheckResult(
            "vllm_bench_serve",
            NOT_READY,
            {"error": repr(exc)},
            reason="`vllm bench serve --help` could not run.",
            fix="Use a vLLM version with the benchmark CLI available.",
        )
    output = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode == 0 and "serve" in output:
        return CheckResult(
            "vllm_bench_serve",
            READY,
            {"returncode": result.returncode},
        )
    return CheckResult(
        "vllm_bench_serve",
        NOT_READY,
        {"returncode": result.returncode, "stderr": result.stderr[-500:]},
        reason="`vllm bench serve` is not available or returned an error.",
        fix="Install a compatible vLLM version or use the fallback benchmark client.",
    )


def _check_vllm_kv_transfer(vllm_cli: CheckResult) -> CheckResult:
    details: dict[str, Any] = {}
    try:
        module = importlib.import_module("vllm.distributed.kv_transfer")
        details["module_path"] = str(getattr(module, "__file__", ""))
        vllm_module = importlib.import_module("vllm")
        vllm_root = Path(str(getattr(vllm_module, "__file__", ""))).resolve().parent
        source_path = vllm_root / "engine" / "arg_utils.py"
        if source_path.exists():
            details["source_path"] = str(source_path)
            details["source_has_kv_transfer_config_flag"] = (
                "--kv-transfer-config" in source_path.read_text(encoding="utf-8")
            )
    except Exception as exc:
        return CheckResult(
            "vllm_kv_transfer",
            NOT_READY,
            {"import_error": repr(exc)},
            reason="vLLM KV-transfer package is not importable.",
            fix=(
                "Install a vLLM version that supports --kv-transfer-config "
                "and vLLM V1 KV connectors."
            ),
        )

    path = vllm_cli.details.get("path")
    if vllm_cli.status != READY or not path:
        return CheckResult(
            "vllm_kv_transfer",
            NOT_READY,
            details,
            reason="vLLM CLI is unavailable, so --kv-transfer-config cannot be checked.",
            fix="Install vLLM with the serving CLI available.",
        )
    try:
        result = subprocess.run(
            [str(path), "serve", "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        details["help_error"] = repr(exc)
        details["help_check"] = "error"
        if details.get("source_has_kv_transfer_config_flag"):
            return CheckResult("vllm_kv_transfer", READY, details)
        return CheckResult(
            "vllm_kv_transfer",
            NOT_READY,
            details,
            reason="`vllm serve --help` could not run for KV-transfer detection.",
            fix="Install a compatible vLLM serving CLI.",
        )
    output = f"{result.stdout}\n{result.stderr}"
    details["returncode"] = result.returncode
    details["has_kv_transfer_config_flag"] = "--kv-transfer-config" in output
    if result.returncode == 0 and details["has_kv_transfer_config_flag"]:
        return CheckResult("vllm_kv_transfer", READY, details)
    if details.get("source_has_kv_transfer_config_flag"):
        return CheckResult("vllm_kv_transfer", READY, details)
    return CheckResult(
        "vllm_kv_transfer",
        NOT_READY,
        details,
        reason="vLLM serving CLI does not expose --kv-transfer-config.",
        fix="Install a vLLM version that supports LMCacheConnectorV1.",
    )


def _check_huggingface_token() -> CheckResult:
    names = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")
    present = [name for name in names if os.environ.get(name)]
    if present:
        return CheckResult("huggingface_token", READY, {"present": True, "variables": present})
    return CheckResult(
        "huggingface_token",
        NOT_READY,
        {"present": False, "variables": list(names)},
        reason="No Hugging Face token environment variable is present.",
        fix="Set HF_TOKEN only if using a token-gated local model workflow.",
    )


def _check_model(model: str | None) -> CheckResult:
    if not model:
        return CheckResult(
            "model",
            SKIPPED,
            {"configured": False},
            reason="No model path or model ID was configured.",
            fix="Pass --model with a local model path for real serving readiness.",
        )
    path = Path(model).expanduser()
    if path.exists():
        readable = os.access(path, os.R_OK)
        status = READY if readable else NOT_READY
        return CheckResult(
            "model",
            status,
            {"configured": True, "value": model, "kind": "local_path", "readable": readable},
            reason="" if readable else "Configured local model path is not readable.",
            fix="" if readable else "Choose a readable local model path.",
        )
    return CheckResult(
        "model",
        UNKNOWN,
        {"configured": True, "value": model, "kind": "model_id_or_missing_path"},
        reason="Configured model does not resolve to a local path; availability is advisory only.",
        fix="Use a local model path for opt-in real serving benchmarks; the doctor will not download models.",
    )


def _check_ports(ports: Sequence[int], host: str = "127.0.0.1") -> CheckResult:
    results: dict[str, bool] = {}
    unavailable: list[int] = []
    for port in ports:
        available = _port_available(host, int(port))
        results[str(port)] = available
        if not available:
            unavailable.append(int(port))
    if not unavailable:
        return CheckResult("ports", READY, {"host": host, "ports": results})
    return CheckResult(
        "ports",
        NOT_READY,
        {"host": host, "ports": results, "unavailable": unavailable},
        reason="One or more serving benchmark ports are already in use.",
        fix="Choose free ports or stop the local process using the port.",
    )


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _readiness(checks: dict[str, CheckResult]) -> dict[str, ReadinessLevel]:
    return {
        "fake_ci_ready": _level(
            checks,
            required=("python", "bifrost_client", "output_directory", "disk_space"),
        ),
        "lmcache_connector_ready": _level(
            checks,
            required=(
                "python",
                "bifrost_client",
                "lmcache_bifrost",
                "lmcache_bifrost_adapter",
                "lmcache_bifrost_config",
                "bifrostd_binary",
                "bifrost_daemon",
                "output_directory",
                "disk_space",
            ),
        ),
        "vllm_import_ready": _level(
            checks,
            required=("python", "torch", "vllm", "lmcache", "vllm_kv_transfer"),
        ),
        "gpu_serving_ready": _gpu_serving_level(checks),
        "full_benchmark_ready": _full_benchmark_level(checks),
    }


def _level(
    checks: dict[str, CheckResult],
    *,
    required: Sequence[str],
) -> ReadinessLevel:
    reasons: list[str] = []
    fixes: list[str] = []
    for name in required:
        check = checks[name]
        if check.status != READY:
            reasons.append(check.reason or f"{name} is {check.status}.")
            if check.fix:
                fixes.append(check.fix)
    return ReadinessLevel(READY if not reasons else NOT_READY, _dedupe(reasons), _dedupe(fixes))


def _gpu_serving_level(checks: dict[str, CheckResult]) -> ReadinessLevel:
    base = _level(
        checks,
        required=(
            "python",
            "torch",
            "vllm",
            "lmcache",
            "vllm_kv_transfer",
            "model",
            "ports",
            "disk_space",
        ),
    )
    reasons = list(base.reasons)
    fixes = list(base.recommended_fixes)
    torch_details = checks["torch"].details
    if checks["torch"].status == READY and not torch_details.get("cuda_available"):
        reasons.append("torch reports CUDA is unavailable.")
        fixes.append("Use a machine with a compatible GPU, driver, CUDA runtime, and torch build.")
    if checks["torch"].status == READY and int(torch_details.get("cuda_device_count") or 0) < 1:
        reasons.append("No GPU devices are visible through torch.")
        fixes.append("Run real serving on a host with at least one visible GPU.")
    return ReadinessLevel(READY if not reasons else NOT_READY, _dedupe(reasons), _dedupe(fixes))


def _full_benchmark_level(checks: dict[str, CheckResult]) -> ReadinessLevel:
    base = _gpu_serving_level(checks)
    required = _level(
        checks,
        required=(
            "lmcache_bifrost",
            "lmcache_bifrost_adapter",
            "lmcache_bifrost_config",
            "bifrost_daemon",
            "vllm_bench_serve",
            "ports",
            "output_directory",
        ),
    )
    reasons = _dedupe([*base.reasons, *required.reasons])
    fixes = _dedupe([*base.recommended_fixes, *required.recommended_fixes])
    return ReadinessLevel(READY if not reasons else NOT_READY, reasons, fixes)


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
