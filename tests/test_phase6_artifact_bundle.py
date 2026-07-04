from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for source_path in (
    REPO_ROOT / "bifrost_py",
    REPO_ROOT / "integrations" / "lmcache_bifrost",
):
    text = str(source_path)
    if text not in sys.path:
        sys.path.insert(0, text)

from bifrost_serving.artifacts import (  # noqa: E402
    REQUIRED_MODE_ARTIFACTS,
    capture_versions,
    redact_mapping,
    verify_artifact_manifest,
    write_artifact_manifest,
)
from bifrost_serving.report import ServingReportConfig, generate_serving_report  # noqa: E402


def test_artifact_manifest_contains_configs_and_hashes_verify(tmp_path: Path) -> None:
    mode_dir = _write_complete_mode_dir(tmp_path / "mode")

    manifest = write_artifact_manifest(mode_dir)
    by_path = {item["relative_path"]: item for item in manifest["artifacts"]}

    assert manifest["missing_required_artifacts"] == []
    for name in (
        "resolved_run_config.yaml",
        "generated_vllm_command.json",
        "generated_lmcache_config.yaml",
        "generated_bifrost_connector_config.json",
    ):
        assert name in by_path
        assert len(by_path[name]["sha256"]) == 64
        assert by_path[name]["byte_size"] > 0
        assert by_path[name]["artifact_type"] == "config"
    assert "artifact_manifest.json" in by_path
    assert verify_artifact_manifest(mode_dir)["status"] == "ok"


def test_report_references_every_generated_config(tmp_path: Path) -> None:
    mode_dir = _write_complete_mode_dir(tmp_path / "mode")
    write_artifact_manifest(mode_dir)

    result = generate_serving_report(ServingReportConfig(run_dir=mode_dir, out=tmp_path / "report"))
    text = (tmp_path / "report" / "report.md").read_text(encoding="utf-8")

    for name in (
        "resolved_run_config.yaml",
        "generated_vllm_command.json",
        "generated_lmcache_config.yaml",
        "generated_bifrost_connector_config.json",
    ):
        assert name in text
        assert name in {
            item["relative_path"]
            for item in result.summary["artifact_bundle"]["configs"]
        }


def test_secrets_are_redacted_in_environment_capture() -> None:
    redacted = redact_mapping(
        {
            "HF_TOKEN": "hf_secret",
            "Authorization": "Bearer abc",
            "normal": "visible",
        }
    )
    versions = capture_versions(
        env={
            "HUGGING_FACE_HUB_TOKEN": "hf_secret",
            "BIFROST_PHASE6": "1",
        }
    )

    assert redacted["HF_TOKEN"] == "<redacted>"
    assert redacted["Authorization"] == "<redacted>"
    assert redacted["normal"] == "visible"
    assert versions["environment"]["HUGGING_FACE_HUB_TOKEN"] == "<redacted>"
    assert versions["environment"]["BIFROST_PHASE6"] == "1"


def _write_complete_mode_dir(mode_dir: Path) -> Path:
    mode_dir.mkdir(parents=True, exist_ok=True)
    for name in REQUIRED_MODE_ARTIFACTS:
        if name == "artifact_manifest.json":
            continue
        path = mode_dir / name
        if name.endswith(".json"):
            _write_json(path, {"name": name})
        elif name.endswith(".jsonl"):
            path.write_text(json.dumps({"name": name}) + "\n", encoding="utf-8")
        else:
            path.write_text(f"name: {name}\n", encoding="utf-8")
    (mode_dir / "generated_lmcache_config.yaml").write_text("mode: inprocess\n", encoding="utf-8")
    (mode_dir / "generated_bifrost_connector_config.json").write_text(
        json.dumps({"endpoint": "127.0.0.1:7420"}) + "\n",
        encoding="utf-8",
    )
    _write_json(
        mode_dir / "summary.json",
        {
            "schema_version": "bifrost.serving_summary.v1",
            "label": "vllm_lmcache_bifrost",
            "backend": "fake",
            "request_count": 1,
            "success_count": 1,
            "error_count": 0,
            "error_rate": 0.0,
            "p50_latency_ms": 1.0,
            "p95_latency_ms": 1.0,
            "mean_latency_ms": 1.0,
            "p50_ttft_ms": None,
            "p95_ttft_ms": None,
            "ttft_available_count": 0,
            "throughput_rps": 1.0,
            "performance_metrics_source": "synthetic_fake_server",
            "connector_metrics_source": "bifrost_connector_jsonl",
            "bifrost_stats_delta": {"object_count": 1},
            "connector_metrics_delta": {"put_count": 1, "bytes_put": 3},
            "bifrost_stats": {
                "before": {"status": "ok", "stats": {"object_count": 0}},
                "after": {"status": "ok", "stats": {"object_count": 1}},
            },
            "phase_sections": {
                "measured": {"request_count": 1, "success_count": 1, "error_count": 0}
            },
        },
    )
    (mode_dir / "raw_requests.jsonl").write_text(
        json.dumps(
            {
                "request_id": "r0",
                "status": 200,
                "latency_ms": 1.0,
                "ttft_ms": None,
                "output_token_count": 1,
                "error": None,
                "metadata": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return mode_dir


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
