from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for source_path in (
    REPO_ROOT / "bifrost_py",
    REPO_ROOT / "integrations" / "lmcache_bifrost",
):
    text = str(source_path)
    if text not in sys.path:
        sys.path.insert(0, text)

from bifrost_serving.baseline_matrix import BaselineMode
from bifrost_serving.real_matrix import (
    RealMatrixConfig,
    RealMatrixSafetyError,
    _matrix_rows,
    evaluate_completion_gate,
    mode_order_for_repetition,
    run_real_matrix,
)
from bifrost_serving.workloads import WorkloadConfig, generate_workload, write_workload

CLI = REPO_ROOT / "tools" / "bifrost_run_phase6_real_matrix.py"


def test_dry_run_generates_three_modes_without_real_dependencies(tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=True)
    before = set(sys.modules)

    result = run_real_matrix(config)

    assert result.status == "dry_run"
    assert result.summary["completion_gate"]["passed"] is False
    assert {item["mode"] for item in result.summary["mode_results"]} == {
        "vllm_only",
        "vllm_lmcache_local_cpu",
        "vllm_lmcache_bifrost",
    }
    assert result.manifest_path.exists()
    assert result.comparison_report_path.exists()
    assert result.evidence_bundle_path.exists()
    assert result.completion_gate_path.exists()
    report = result.output_dir.joinpath("report.md").read_text(encoding="utf-8")
    assert "## Environment" in report
    assert "## Generated Configs" in report
    assert "generated_vllm_command.json" in report
    imported = set(sys.modules) - before
    assert "vllm" not in imported
    assert "lmcache" not in imported


def test_dry_run_captures_exact_fairness_config_equality(tmp_path: Path) -> None:
    result = run_real_matrix(_config(tmp_path, dry_run=True))
    command_files = [
        Path(item["output_dir"]) / "generated_vllm_command.json"
        for item in result.summary["mode_results"]
    ]
    commands = [json.loads(path.read_text(encoding="utf-8")) for path in command_files]
    core_flags = [command["vllm_core_flags"] for command in commands]

    assert core_flags[1:] == [core_flags[0], core_flags[0]]
    assert all("--no-enable-prefix-caching" in command["command"] for command in commands)
    assert result.summary["workload"]["sha256"]
    assert len({result.summary["workload"]["sha256"]}) == 1

    lmcache_configs = {
        item["mode"]: (Path(item["output_dir"]) / "generated_lmcache_config.yaml").read_text(
            encoding="utf-8"
        )
        for item in result.summary["mode_results"]
        if item["mode"] != "vllm_only"
    }
    assert "mode: inprocess" in lmcache_configs["vllm_lmcache_local_cpu"]
    assert "mode: inprocess" in lmcache_configs["vllm_lmcache_bifrost"]


def test_rotated_order_is_deterministic() -> None:
    modes = (
        BaselineMode.VLLM_ONLY,
        BaselineMode.VLLM_LMCACHE_LOCAL_CPU,
        BaselineMode.VLLM_LMCACHE_BIFROST,
    )

    assert mode_order_for_repetition(modes, 0, rotate=True) == modes
    assert mode_order_for_repetition(modes, 1, rotate=True) == (
        BaselineMode.VLLM_LMCACHE_LOCAL_CPU,
        BaselineMode.VLLM_LMCACHE_BIFROST,
        BaselineMode.VLLM_ONLY,
    )
    assert mode_order_for_repetition(modes, 2, rotate=True) == (
        BaselineMode.VLLM_LMCACHE_BIFROST,
        BaselineMode.VLLM_ONLY,
        BaselineMode.VLLM_LMCACHE_LOCAL_CPU,
    )
    assert mode_order_for_repetition(modes, 3, rotate=True) == modes


def test_real_execution_refuses_without_opt_in(tmp_path: Path) -> None:
    with pytest.raises(RealMatrixSafetyError, match="refusing real execution"):
        run_real_matrix(_config(tmp_path, dry_run=False))


def test_missing_gpu_preflight_marks_real_run_incomplete_without_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(
        "bifrost_serving.real_matrix.run_doctor",
        lambda config: _Doctor(_not_ready_doctor()),
    )

    result = run_real_matrix(
        _config(tmp_path, dry_run=False, allow_real_vllm=True)
    )

    assert result.status == "failed"
    assert result.summary["preflight"]["status"] == "not_ready"
    assert any("GPU is not visible" in item for item in result.summary["preflight"]["failures"])
    assert all(item["status"] == "skipped" for item in result.summary["mode_results"])


def test_completion_gate_is_incomplete_when_a_mode_is_skipped() -> None:
    gate = evaluate_completion_gate(
        mode_results=[
            {
                "mode": "vllm_only",
                "repetition": 0,
                "status": "skipped",
                "skip_reason": "preflight failed",
            }
        ],
        requested_modes=(BaselineMode.VLLM_ONLY,),
        repetitions=1,
        dry_run=False,
        correctness={"status": "pass"},
    )

    assert gate["passed"] is False
    assert gate["status"] == "failed"
    assert "did not complete" in gate["failures"][0]


def test_completion_gate_accepts_nested_bifrost_connector_put_get_activity() -> None:
    gate = evaluate_completion_gate(
        mode_results=[
            {
                "mode": "vllm_lmcache_bifrost",
                "repetition": 0,
                "status": "completed",
                "summary": {
                    "request_count": 40,
                    "bifrost_stats_delta": {"object_count": 8, "bytes_stored": 1024},
                    "phase_sections": {"measured": {"request_count": 40}},
                    "bifrost_stats": {
                        "after": {
                            "fsck": {"status": "ok"},
                            "connector_metrics": {
                                "status": "ok",
                                "stats": {"put_count": 8, "get_count": 40},
                            },
                        }
                    },
                },
                "artifact_manifest": {"missing_required_artifacts": []},
            }
        ],
        requested_modes=(BaselineMode.VLLM_LMCACHE_BIFROST,),
        repetitions=1,
        dry_run=False,
        correctness={"status": "pass"},
    )

    assert gate["passed"] is True
    assert gate["status"] == "pass"


def test_completion_gate_rejects_bifrost_connector_activity_without_store_activity() -> None:
    gate = evaluate_completion_gate(
        mode_results=[
            {
                "mode": "vllm_lmcache_bifrost",
                "repetition": 0,
                "status": "completed",
                "summary": {
                    "request_count": 40,
                    "bifrost_stats_delta": {"object_count": 0, "bytes_stored": 0},
                    "phase_sections": {"measured": {"request_count": 40}},
                    "bifrost_stats": {
                        "after": {
                            "fsck": {"status": "ok"},
                            "connector_metrics": {
                                "status": "ok",
                                "stats": {"put_count": 8, "get_count": 40},
                            },
                        }
                    },
                },
                "artifact_manifest": {"missing_required_artifacts": []},
            }
        ],
        requested_modes=(BaselineMode.VLLM_LMCACHE_BIFROST,),
        repetitions=1,
        dry_run=False,
        correctness={"status": "pass"},
    )

    assert gate["passed"] is False
    assert any("store object or byte activity" in failure for failure in gate["failures"])


def test_matrix_rows_use_lmcache_log_activity_when_metric_delta_is_unavailable() -> None:
    rows = _matrix_rows(
        {
            "mode_results": [
                {
                    "mode": "vllm_lmcache_local_cpu",
                    "repetition": 0,
                    "status": "completed",
                    "summary": {
                        "request_count": 40,
                        "lmcache_activity": {"store_activity": False, "retrieve_activity": False},
                        "lmcache_log_activity": {
                            "store_activity": True,
                            "retrieve_activity": True,
                        },
                    },
                }
            ],
            "workload": {"sha256": "abc123"},
        }
    )

    assert rows[0]["lmcache_store_activity"] is True
    assert rows[0]["lmcache_retrieve_activity"] is True


def test_matrix_rows_include_bifrost_store_deltas() -> None:
    rows = _matrix_rows(
        {
            "mode_results": [
                {
                    "mode": "vllm_lmcache_bifrost",
                    "repetition": 0,
                    "status": "completed",
                    "summary": {
                        "request_count": 40,
                        "bifrost_stats_delta": {
                            "object_count": 40,
                            "bytes_stored": 1174413240,
                        },
                    },
                }
            ],
            "workload": {"sha256": "abc123"},
        }
    )

    assert rows[0]["bifrost_store_object_delta"] == 40
    assert rows[0]["bifrost_store_bytes_delta"] == 1174413240


def test_cli_dry_run_does_not_start_vllm_in_ci(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    workload = _workload(tmp_path)
    output_dir = tmp_path / "matrix"

    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--workload-jsonl",
            str(workload),
            "--output-dir",
            str(output_dir),
            "--model",
            str(tmp_path / "missing-model"),
            "--dry-run",
            "--repetitions",
            "1",
            "--json",
        ],
        cwd=REPO_ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 1, result.stderr
    data = json.loads(result.stdout)
    assert data["dry_run"] is True
    assert data["completion_gate"]["status"] == "dry_run"
    assert all(item["status"] == "planned" for item in data["mode_results"])
    assert "vllm" not in result.stderr.lower()


def _config(
    tmp_path: Path,
    *,
    dry_run: bool,
    allow_real_vllm: bool = False,
) -> RealMatrixConfig:
    workload = _workload(tmp_path)
    model = tmp_path / "local-model"
    model.mkdir(exist_ok=True)
    return RealMatrixConfig(
        workload_jsonl=workload,
        output_dir=tmp_path / "matrix",
        model=str(model),
        served_model_name="phase6-test-model",
        dtype="float16",
        max_model_len=2048,
        output_len=8,
        concurrency=2,
        request_rate=4.0,
        repetitions=1,
        rotate_mode_order=False,
        base_port=18100,
        bifrost_base_port=17744,
        port_stride=20,
        min_free_disk_bytes=1,
        allow_real_vllm=allow_real_vllm,
        dry_run=dry_run,
        engine_warmup_requests=1,
        population_requests_per_prefix=1,
    )


def _workload(tmp_path: Path) -> Path:
    path = tmp_path / "workload.jsonl"
    if not path.exists():
        workload = generate_workload(
            WorkloadConfig(
                workload_name="fake_ci_small",
                request_count=6,
                prefix_repeat_groups=2,
                max_tokens=8,
                seed=2026,
            )
        )
        write_workload(workload, out=path)
    return path


def _not_ready_doctor() -> dict[str, object]:
    ready = {"status": "ready", "details": {}}
    return {
        "checks": {
            "torch": {
                "status": "ready",
                "details": {"cuda_available": False, "cuda_device_count": 0},
            },
            "vllm": ready,
            "lmcache": ready,
            "lmcache_bifrost": ready,
            "lmcache_bifrost_adapter": ready,
            "bifrostd_binary": ready,
            "disk_space": ready,
            "ports": ready,
            "model": ready,
        },
        "readiness": {},
    }


class _Doctor:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def to_dict(self) -> dict[str, object]:
        return self._data
