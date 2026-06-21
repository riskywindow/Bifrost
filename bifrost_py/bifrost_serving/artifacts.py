"""Reproducibility artifact bundle helpers for Phase 6 serving runs."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

REQUIRED_MODE_ARTIFACTS: tuple[str, ...] = (
    "resolved_run_config.yaml",
    "generated_vllm_command.json",
    "workload.jsonl",
    "phase_plan.json",
    "environment_doctor.json",
    "versions.json",
    "command_manifest.json",
    "metrics_before.json",
    "metrics_after_population.json",
    "metrics_after_measured.json",
    "raw_requests.jsonl",
    "stdout.log",
    "stderr.log",
    "artifact_manifest.json",
)

OPTIONAL_MODE_ARTIFACTS: tuple[str, ...] = (
    "generated_lmcache_config.yaml",
    "generated_bifrost_connector_config.json",
)

CONFIG_ARTIFACTS: tuple[str, ...] = (
    "resolved_run_config.yaml",
    "generated_vllm_command.json",
    "generated_lmcache_config.yaml",
    "generated_bifrost_connector_config.json",
)

SECRET_FRAGMENTS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "hf_token",
    "hugging_face_hub_token",
    "secret",
    "token",
)


@dataclass(frozen=True, slots=True)
class ArtifactEntry:
    relative_path: str
    sha256: str
    byte_size: int
    artifact_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "artifact_type": self.artifact_type,
        }


def write_artifact_manifest(
    mode_dir: Path,
    *,
    include_missing: bool = True,
) -> dict[str, Any]:
    manifest = build_artifact_manifest(mode_dir, include_missing=include_missing)
    path = mode_dir / "artifact_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = build_artifact_manifest(mode_dir, include_missing=include_missing)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def build_artifact_manifest(
    mode_dir: Path,
    *,
    include_missing: bool = True,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    missing: list[str] = []
    expected = [*REQUIRED_MODE_ARTIFACTS, *OPTIONAL_MODE_ARTIFACTS]
    seen: set[str] = set()
    for relative in expected:
        path = mode_dir / relative
        if path.exists() and path.is_file():
            entries.append(artifact_entry(path, root=mode_dir).to_dict())
            seen.add(relative)
        elif relative in REQUIRED_MODE_ARTIFACTS and relative != "artifact_manifest.json":
            missing.append(relative)
    for path in sorted(mode_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(mode_dir).as_posix()
        if relative in seen:
            continue
        if relative == "artifact_manifest.json":
            entries.append(artifact_entry(path, root=mode_dir).to_dict())
        elif include_missing and relative not in expected:
            entries.append(artifact_entry(path, root=mode_dir).to_dict())
    return {
        "schema_version": "bifrost.phase6_artifact_manifest.v1",
        "mode_dir": str(mode_dir),
        "artifacts": sorted(entries, key=lambda item: item["relative_path"]),
        "missing_required_artifacts": sorted(missing),
    }


def artifact_entry(path: Path, *, root: Path) -> ArtifactEntry:
    data = path.read_bytes()
    relative = path.relative_to(root).as_posix()
    return ArtifactEntry(
        relative_path=relative,
        sha256=hashlib.sha256(data).hexdigest(),
        byte_size=len(data),
        artifact_type=artifact_type(relative),
    )


def verify_artifact_manifest(mode_dir: Path, manifest: Mapping[str, Any] | None = None) -> dict[str, Any]:
    manifest_data = dict(manifest) if manifest is not None else _read_manifest(mode_dir)
    failures: list[dict[str, Any]] = []
    for item in manifest_data.get("artifacts", []):
        if not isinstance(item, dict):
            continue
        relative = item.get("relative_path")
        if not isinstance(relative, str):
            continue
        if relative == "artifact_manifest.json":
            continue
        path = mode_dir / relative
        if not path.exists():
            failures.append({"relative_path": relative, "reason": "missing"})
            continue
        actual = artifact_entry(path, root=mode_dir)
        if actual.sha256 != item.get("sha256") or actual.byte_size != item.get("byte_size"):
            failures.append({"relative_path": relative, "reason": "hash_or_size_mismatch"})
    return {
        "status": "ok" if not failures else "error",
        "failure_count": len(failures),
        "failures": failures,
    }


def write_json_artifact(path: Path, payload: Mapping[str, Any] | Sequence[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def capture_versions(
    *,
    model: str | None = None,
    workload_path: Path | None = None,
    bifrostd_path: str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    environment = redact_mapping(dict(env if env is not None else os.environ))
    return {
        "schema_version": "bifrost.phase6_versions.v1",
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "operating_system": {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "cuda": _cuda_details(),
        "gpu": _gpu_details(),
        "packages": {
            "vllm": _package_version("vllm"),
            "lmcache": _first_package_version(("lmcache", "lmcache-vllm")),
            "lmcache_bifrost": _package_version("lmcache-bifrost"),
            "torch": _package_version("torch"),
        },
        "bifrostd": _bifrostd_details(bifrostd_path),
        "model": _model_details(model),
        "workload": _workload_details(workload_path),
        "git": _git_details(),
        "environment": environment,
    }


def redact_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): redact_value(str(key), value) for key, value in mapping.items()}


def redact_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(fragment in lowered for fragment in SECRET_FRAGMENTS):
        return "<redacted>"
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, list):
        return [redact_value(key, item) for item in value]
    if isinstance(value, str) and _looks_secret(value):
        return "<redacted>"
    return value


def artifact_type(relative_path: str) -> str:
    name = Path(relative_path).name
    if name in CONFIG_ARTIFACTS or name.endswith("_config.json") or name.endswith("_config.yaml"):
        return "config"
    if name in {"workload.jsonl", "raw_requests.jsonl"}:
        return "workload" if name == "workload.jsonl" else "raw_metrics"
    if name.startswith("metrics_"):
        return "metrics"
    if name in {"environment_doctor.json", "versions.json"}:
        return "environment"
    if name in {"phase_plan.json", "command_manifest.json"}:
        return "provenance"
    if name in {"stdout.log", "stderr.log"}:
        return "log"
    if name == "artifact_manifest.json":
        return "manifest"
    return "artifact"


def _read_manifest(mode_dir: Path) -> dict[str, Any]:
    path = mode_dir / "artifact_manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _package_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _first_package_version(distributions: tuple[str, ...]) -> str | None:
    for distribution in distributions:
        version = _package_version(distribution)
        if version is not None:
            return version
    return None


def _git_details() -> dict[str, Any]:
    root = _run(["git", "rev-parse", "--show-toplevel"])
    commit = _run(["git", "rev-parse", "HEAD"])
    dirty = _run(["git", "status", "--porcelain"])
    return {
        "repository": root.stdout.strip() if root.returncode == 0 else None,
        "commit": commit.stdout.strip() if commit.returncode == 0 else None,
        "dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
    }


def _cuda_details() -> dict[str, Any]:
    nvcc = shutil.which("nvcc")
    version = None
    if nvcc:
        result = _run([nvcc, "--version"])
        version = result.stdout.strip() if result.returncode == 0 else None
    return {"version": version, "nvcc_path": nvcc}


def _gpu_details() -> dict[str, Any]:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return {"available": False}
    result = _run(
        [
            nvidia_smi,
            "--query-gpu=name,driver_version",
            "--format=csv,noheader",
        ]
    )
    if result.returncode != 0:
        return {"available": False, "nvidia_smi_path": nvidia_smi}
    rows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return {"available": bool(rows), "nvidia_smi_path": nvidia_smi, "devices": rows}


def _bifrostd_details(path: str | None) -> dict[str, Any]:
    resolved = path or shutil.which("bifrost-daemon") or shutil.which("bifrostd")
    if not resolved:
        return {"path": None, "version": None}
    version = None
    result = _run([resolved, "--version"])
    if result.returncode == 0:
        version = (result.stdout or result.stderr).strip()
    return {"path": resolved, "version": version}


def _model_details(model: str | None) -> dict[str, Any]:
    if not model:
        return {"value": None, "status": "unavailable"}
    path = Path(model).expanduser()
    local = path.exists()
    return {
        "value": model,
        "status": "local" if local else "remote_or_identifier",
        "local": local,
    }


def _workload_details(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"path": str(path) if path else None, "sha256": None}
    data = path.read_bytes()
    return {
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
    }


def _looks_secret(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("hf_") or lowered.startswith("bearer ")


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


__all__ = [
    "CONFIG_ARTIFACTS",
    "OPTIONAL_MODE_ARTIFACTS",
    "REQUIRED_MODE_ARTIFACTS",
    "ArtifactEntry",
    "artifact_entry",
    "artifact_type",
    "build_artifact_manifest",
    "capture_versions",
    "redact_mapping",
    "redact_value",
    "verify_artifact_manifest",
    "write_artifact_manifest",
    "write_json_artifact",
]
