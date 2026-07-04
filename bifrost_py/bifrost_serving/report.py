"""Polished Phase 6 serving benchmark report generation."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .artifacts import CONFIG_ARTIFACTS, artifact_entry, verify_artifact_manifest
from .metrics import MetricSource

REPORT_FORMATS = {"markdown", "json", "csv", "all"}


@dataclass(frozen=True, slots=True)
class ServingReportConfig:
    run_dir: Path
    comparison_dir: Path | None = None
    out: Path | None = None
    format: str = "all"


@dataclass(frozen=True, slots=True)
class ServingReportResult:
    output_dir: Path
    summary: dict[str, Any]
    report_path: Path | None = None
    summary_path: Path | None = None
    per_request_csv_path: Path | None = None
    comparison_csv_path: Path | None = None


class ServingReportError(RuntimeError):
    """Deterministic Phase 6 report generation failure."""


def generate_serving_report(config: ServingReportConfig) -> ServingReportResult:
    _validate_config(config)
    output_dir = config.out or (config.run_dir / "serving_report")
    output_dir.mkdir(parents=True, exist_ok=True)

    run_summary = _read_json(config.run_dir / "summary.json")
    raw_requests = _read_jsonl(config.run_dir / "raw_requests.jsonl")
    comparison_summary = _read_comparison(config.comparison_dir)
    summary = build_report_summary(
        run_dir=config.run_dir,
        run_summary=run_summary,
        raw_requests=raw_requests,
        comparison_dir=config.comparison_dir,
        comparison_summary=comparison_summary,
    )

    report_path: Path | None = None
    summary_path: Path | None = None
    per_request_path: Path | None = None
    comparison_path: Path | None = None
    requested = {config.format}
    if config.format == "all":
        requested = {"markdown", "json", "csv"}
    if "markdown" in requested:
        report_path = output_dir / "report.md"
        report_path.write_text(render_markdown(summary) + "\n", encoding="utf-8")
    if "json" in requested:
        summary_path = output_dir / "summary.json"
        _write_json(summary_path, summary)
    if "csv" in requested:
        per_request_path = output_dir / "per_request.csv"
        write_per_request_csv(per_request_path, raw_requests)
        if comparison_summary is not None:
            comparison_path = output_dir / "comparison.csv"
            write_comparison_csv(comparison_path, comparison_summary)

    return ServingReportResult(
        output_dir=output_dir,
        summary=summary,
        report_path=report_path,
        summary_path=summary_path,
        per_request_csv_path=per_request_path,
        comparison_csv_path=comparison_path,
    )


def build_report_summary(
    *,
    run_dir: Path,
    run_summary: dict[str, Any],
    raw_requests: list[dict[str, Any]],
    comparison_dir: Path | None,
    comparison_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    comparison = _comparison_summary(comparison_dir, comparison_summary)
    return {
        "schema_version": "bifrost.serving_report.v1",
        "generated_at": now,
        "run_dir": str(run_dir),
        "comparison_dir": str(comparison_dir) if comparison_dir else None,
        "artifacts": {
            "source_summary": str(run_dir / "summary.json"),
            "source_raw_requests": str(run_dir / "raw_requests.jsonl"),
            "source_config": str(run_dir / "config.json"),
            "source_workload": str(run_dir / "workload.jsonl"),
            "source_comparison": (
                str(comparison_dir / "comparison_summary.json") if comparison_dir else None
            ),
        },
        "artifact_bundle": _artifact_bundle_summary(run_dir, comparison_summary),
        "environment": _environment_summary(run_summary),
        "scenario": _scenario_summary(run_summary, comparison_summary),
        "workload": _workload_summary(run_summary),
        "metric_sources": _metric_sources_summary(run_summary, comparison_summary),
        "phase_counts": _phase_counts(run_summary),
        "mode_summary": _mode_summary(run_summary, comparison_summary),
        "latency": _latency_summary(run_summary),
        "bifrost_activity": _bifrost_activity_summary(run_summary),
        "correctness": _correctness_summary(run_summary, raw_requests),
        "skipped_components": _skipped_components(run_summary, comparison_summary),
        "limitations_and_notes": _limitations_and_notes(run_summary, comparison_summary),
        "comparison": comparison,
        "per_request_count": len(raw_requests),
    }


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# BIFROST Phase 6 Serving Benchmark Report",
        "",
        f"Generated: `{summary['generated_at']}`",
        "",
        "## Environment Summary",
        "",
    ]
    env = summary["environment"]
    lines.extend(
        [
            f"- Repository: {_fmt(env.get('repository'))}",
            f"- Git commit: {_fmt(env.get('git_commit'))}",
            f"- Dirty tree: {_fmt(env.get('dirty_tree'))}",
            f"- Python: {_fmt(env.get('python_version'))}",
            f"- Platform: {_fmt(env.get('platform'))}",
            f"- Torch: {_fmt(env.get('torch_version'))}",
            f"- CUDA available: {_fmt(env.get('cuda_available'))}",
            f"- CUDA version: {_fmt(env.get('cuda_version'))}",
            f"- Driver version: {_fmt(env.get('driver_version'))}",
            f"- GPU: {_fmt(env.get('gpu'))}",
            f"- vLLM: {_fmt(env.get('vllm_version'))}",
            f"- LMCache: {_fmt(env.get('lmcache_version'))}",
            f"- BIFROST connector: {_fmt(env.get('connector_version'))}",
            f"- bifrostd: {_fmt(env.get('bifrostd'))}",
            f"- Environment readiness: {_fmt(env.get('readiness'))}",
            "",
            "## Scenario Summary",
            "",
        ]
    )
    scenario = summary["scenario"]
    lines.extend(
        [
            f"- Label: {_fmt(scenario.get('label'))}",
            f"- Backend: {_fmt(scenario.get('backend'))}",
            f"- Base URL: {_fmt(scenario.get('base_url'))}",
            f"- Endpoint: {_fmt(scenario.get('endpoint'))}",
            f"- Model: {_fmt(scenario.get('model'))}",
            f"- Local model asset status: {_fmt(scenario.get('model_local_asset_statement'))}",
            f"- Run duration seconds: {_fmt_num(scenario.get('run_duration_s'))}",
            f"- Real vLLM: {_fmt(scenario.get('real_vllm_status'))}",
            "",
            "## Reproducibility Bundle",
            "",
        ]
    )
    bundle = summary["artifact_bundle"]
    lines.extend(
        [
            f"- Artifact manifest: {_fmt(bundle.get('manifest_path'))}",
            f"- Artifact verification: {_fmt(bundle.get('verification_status'))}",
            f"- Missing required artifacts: {_fmt(', '.join(bundle.get('missing_required_artifacts') or []))}",
            f"- Common config equality: {_fmt(bundle.get('common_config_equality'))}",
            "",
            "| Config | SHA-256 | Bytes |",
            "| --- | --- | ---: |",
        ]
    )
    configs = bundle.get("configs") if isinstance(bundle.get("configs"), list) else []
    if configs:
        for item in configs:
            lines.append(
                "| {path} | {sha} | {size} |".format(
                    path=_md_code(item.get("relative_path")),
                    sha=_md_code(item.get("sha256")),
                    size=_fmt_num(item.get("byte_size")),
                )
            )
    else:
        lines.append("| unavailable | unavailable | unavailable |")
    lines.extend(
        [
            "",
            "## Metric Sources",
            "",
            "| Metric group | Source | Status | Note |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in summary["metric_sources"]:
        lines.append(
            "| {group} | {source} | {status} | {note} |".format(
                group=_md_code(row.get("group")),
                source=_md_code(row.get("source")),
                status=_md_code(row.get("status")),
                note=_escape_pipes(_fmt(row.get("note"))),
            )
        )
    lines.extend(
        [
            "",
            "## Phase Counts",
            "",
            "| Phase | Requests | Successes | Errors |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for row in summary["phase_counts"]:
        lines.append(
            "| {phase} | {requests} | {successes} | {errors} |".format(
                phase=_md_code(row.get("phase")),
                requests=_fmt_num(row.get("request_count")),
                successes=_fmt_num(row.get("success_count")),
                errors=_fmt_num(row.get("error_count")),
            )
        )
    lines.extend(
        [
            "",
            "## Workload Summary",
            "",
        ]
    )
    workload = summary["workload"]
    lines.extend(
        [
            f"- Requests: {_fmt(workload.get('request_count'))}",
            f"- Successes: {_fmt(workload.get('success_count'))}",
            f"- Errors: {_fmt(workload.get('error_count'))}",
            f"- Expected cache reuse requests: {_fmt(workload.get('cache_expected_request_count'))}",
            f"- Repeated prefix groups: {_fmt(workload.get('repeated_prefix_group_count'))}",
            f"- Workload name: {_fmt(workload.get('workload_name'))}",
            f"- Max tokens: {_fmt(workload.get('max_tokens_values'))}",
            "",
            "## Mode Summary",
            "",
            "| Mode | Status | Requests | p50 latency ms | p95 latency ms | p50 TTFT ms | Error rate | Notes |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in summary["mode_summary"]:
        lines.append(
            "| {mode} | {status} | {requests} | {p50} | {p95} | {ttft} | {error} | {notes} |".format(
                mode=_md_code(row.get("mode")),
                status=_md_code(row.get("status")),
                requests=_fmt(row.get("request_count")),
                p50=_fmt_num(row.get("p50_latency_ms")),
                p95=_fmt_num(row.get("p95_latency_ms")),
                ttft=_fmt_num(row.get("p50_ttft_ms")),
                error=_fmt_num(row.get("error_rate")),
                notes=_escape_pipes(row.get("notes") or ""),
            )
        )
    lines.extend(
        [
            "",
            "## Latency Table",
            "",
            "| p50 latency ms | p95 latency ms | mean latency ms | TTFT p50 ms | TTFT p95 ms | throughput RPS | error rate |",
            "| ---: | ---: | ---: | --- | --- | ---: | ---: |",
        ]
    )
    latency = summary["latency"]
    lines.append(
        "| {p50} | {p95} | {mean} | {ttftp50} | {ttftp95} | {rps} | {err} |".format(
            p50=_fmt_num(latency.get("p50_latency_ms")),
            p95=_fmt_num(latency.get("p95_latency_ms")),
            mean=_fmt_num(latency.get("mean_latency_ms")),
            ttftp50=_fmt_num(latency.get("p50_ttft_ms")),
            ttftp95=_fmt_num(latency.get("p95_ttft_ms")),
            rps=_fmt_num(latency.get("throughput_rps")),
            err=_fmt_num(latency.get("error_rate")),
        )
    )
    if latency.get("ttft_status") != "available":
        lines.append("")
        lines.append(f"TTFT is {latency['ttft_status']}: {latency['ttft_note']}")
    lines.extend(
        [
            "",
            "## BIFROST Activity",
            "",
            "| put count | get count | exists count | bytes stored | bytes loaded | object count delta | fsck status |",
            "| ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    activity = summary["bifrost_activity"]
    lines.append(
        "| {put} | {get} | {exists} | {stored} | {loaded} | {objects} | {fsck} |".format(
            put=_fmt_num(activity.get("put_count")),
            get=_fmt_num(activity.get("get_count")),
            exists=_fmt_num(activity.get("exists_count")),
            stored=_fmt_num(activity.get("bytes_stored")),
            loaded=_fmt_num(activity.get("bytes_loaded")),
            objects=_fmt_num(activity.get("object_count_delta")),
            fsck=_fmt(activity.get("fsck_status")),
        )
    )
    if activity.get("status") != "available":
        lines.extend(["", f"BIFROST stats are {activity['status']}: {activity['note']}"])
    if activity.get("synthetic_label"):
        lines.extend(["", "Synthetic fake-server timing metrics are labeled separately and are not connector metrics."])
    lines.extend(
        [
            "",
            "## Correctness Summary",
            "",
        ]
    )
    correctness = summary["correctness"]
    lines.extend(f"- {item}" for item in correctness["notes"])
    lines.extend(["", "## Skipped Components", ""])
    skipped = summary["skipped_components"]
    lines.extend(f"- {item}" for item in skipped) if skipped else lines.append("- None")
    lines.extend(["", "## Limitations And Notes", ""])
    notes = summary["limitations_and_notes"]
    lines.extend(f"- {item}" for item in notes) if notes else lines.append("- None")
    return "\n".join(lines)


def write_per_request_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        "request_id",
        "status",
        "latency_ms",
        "ttft_ms",
        "output_token_count",
        "error",
        "workload_name",
        "prefix_id",
        "repeat_group",
        "expected_cache_reuse",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            writer.writerow(
                {
                    "request_id": row.get("request_id"),
                    "status": row.get("status"),
                    "latency_ms": row.get("latency_ms"),
                    "ttft_ms": row.get("ttft_ms"),
                    "output_token_count": row.get("output_token_count"),
                    "error": row.get("error"),
                    "workload_name": metadata.get("workload_name"),
                    "prefix_id": metadata.get("prefix_id"),
                    "repeat_group": metadata.get("repeat_group"),
                    "expected_cache_reuse": metadata.get("expected_cache_reuse"),
                }
            )


def write_comparison_csv(path: Path, comparison_summary: dict[str, Any]) -> None:
    columns = [
        "baseline_mode",
        "candidate_mode",
        "status",
        "latency_delta_ms",
        "latency_delta_pct",
        "ttft_delta_ms",
        "ttft_delta_pct",
        "error_rate_delta",
        "cache_activity_observed",
        "skipped_reason",
        "notes",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for item in comparison_summary.get("comparisons", []):
            writer.writerow(
                {
                    key: ("; ".join(item.get(key, [])) if key == "notes" else item.get(key))
                    for key in columns
                }
            )


def _artifact_bundle_summary(
    run_dir: Path,
    comparison_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    manifest_path = run_dir / "artifact_manifest.json"
    manifest: dict[str, Any] | None = None
    verification = {"status": "unavailable", "failure_count": None, "failures": []}
    if manifest_path.exists():
        manifest = _read_json(manifest_path)
        verification = verify_artifact_manifest(run_dir, manifest)
    artifacts = manifest.get("artifacts", []) if isinstance(manifest, dict) else []
    configs = [
        item
        for item in artifacts
        if isinstance(item, dict)
        and (
            item.get("artifact_type") == "config"
            or item.get("relative_path") in CONFIG_ARTIFACTS
        )
    ]
    if not configs:
        configs = _legacy_config_entries(run_dir)
    return {
        "manifest_path": str(manifest_path) if manifest_path.exists() else None,
        "verification_status": verification.get("status"),
        "verification_failures": verification.get("failures", []),
        "missing_required_artifacts": (
            manifest.get("missing_required_artifacts", []) if isinstance(manifest, dict) else []
        ),
        "configs": configs,
        "common_config_equality": _common_config_equality(run_dir, comparison_summary),
    }


def _metric_sources_summary(
    run_summary: dict[str, Any],
    comparison_summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    performance_source = run_summary.get("performance_metrics_source")
    connector_source = run_summary.get("connector_metrics_source")
    rows.append(
        _source_row(
            "performance",
            performance_source,
            "synthetic fake metrics" if performance_source == MetricSource.SYNTHETIC_FAKE_SERVER.value else "",
        )
    )
    rows.append(_source_row("bifrost_connector", connector_source, "connector counters"))
    bifrost_stats = run_summary.get("bifrost_stats") if isinstance(run_summary.get("bifrost_stats"), dict) else {}
    after = bifrost_stats.get("after") if isinstance(bifrost_stats.get("after"), dict) else {}
    rows.append(
        _source_row(
            "bifrost_store",
            MetricSource.BIFROST_STORE_STATS.value if after.get("status") == "ok" else None,
            after.get("reason") or "store stats before/after",
        )
    )
    backend_metrics = run_summary.get("backend_metrics") if isinstance(run_summary.get("backend_metrics"), dict) else {}
    backend_after = backend_metrics.get("after") if isinstance(backend_metrics.get("after"), dict) else {}
    rows.append(
        _source_row(
            "synthetic_fake_server",
            MetricSource.SYNTHETIC_FAKE_SERVER.value if backend_after.get("status") == "ok" else None,
            "fake server counters are not connector counters",
        )
    )
    if comparison_summary is not None:
        for item in comparison_summary.get("mode_results", []):
            if not isinstance(item, dict):
                continue
            summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
            rows.append(
                _source_row(
                    f"mode:{item.get('mode')}",
                    summary.get("performance_metrics_source") or item.get("performance_metrics_source"),
                    item.get("status"),
                )
            )
    return rows


def _phase_counts(run_summary: dict[str, Any]) -> list[dict[str, Any]]:
    sections = run_summary.get("phase_sections")
    if not isinstance(sections, dict):
        return [
            {
                "phase": run_summary.get("phase") or "measured",
                "request_count": run_summary.get("request_count"),
                "success_count": run_summary.get("success_count"),
                "error_count": run_summary.get("error_count"),
            }
        ]
    rows = []
    for phase, section in sections.items():
        if not isinstance(section, dict):
            continue
        rows.append(
            {
                "phase": phase,
                "request_count": section.get("request_count"),
                "success_count": section.get("success_count"),
                "error_count": section.get("error_count"),
            }
        )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a Phase 6 serving benchmark report")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--comparison-dir", default=None, type=Path)
    parser.add_argument("--out", default=None, type=Path)
    parser.add_argument("--format", choices=sorted(REPORT_FORMATS), default="all")
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
        result = generate_serving_report(
            ServingReportConfig(
                run_dir=args.run_dir,
                comparison_dir=args.comparison_dir,
                out=args.out,
                format=args.format,
            )
        )
        if args.json:
            print(json.dumps(result.summary, indent=2, sort_keys=True))
        else:
            print(f"wrote Phase 6 serving report artifacts in {result.output_dir}")
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        print(f"bifrost serving report failed: {exc}", file=sys.stderr)
        return 2


def _environment_summary(run_summary: dict[str, Any]) -> dict[str, Any]:
    doctor = run_summary.get("environment_doctor")
    checks = doctor.get("checks", {}) if isinstance(doctor, dict) else {}
    readiness = doctor.get("readiness", {}) if isinstance(doctor, dict) else {}
    git = _details(checks, "git")
    python = _details(checks, "python")
    platform = _details(checks, "platform")
    torch = _details(checks, "torch")
    vllm = _details(checks, "vllm")
    lmcache = _details(checks, "lmcache")
    connector = _details(checks, "lmcache_bifrost")
    bifrostd = _details(checks, "bifrostd_binary")
    fake = readiness.get("fake_ci_ready") if isinstance(readiness, dict) else None
    gpu = readiness.get("gpu_serving_ready") if isinstance(readiness, dict) else None
    full = readiness.get("full_benchmark_ready") if isinstance(readiness, dict) else None
    legacy_real = readiness.get("real_serving_ready") if isinstance(readiness, dict) else None
    gpu_names = torch.get("gpu_names") if isinstance(torch.get("gpu_names"), list) else []
    return {
        "repository": git.get("repository"),
        "git_commit": git.get("commit"),
        "dirty_tree": git.get("dirty"),
        "python_version": python.get("version"),
        "python_executable": python.get("executable"),
        "platform": platform.get("platform"),
        "machine": platform.get("machine"),
        "cpu_count": platform.get("cpu_count"),
        "memory_bytes": platform.get("memory_bytes"),
        "torch_version": torch.get("version"),
        "cuda_available": torch.get("cuda_available"),
        "cuda_version": torch.get("cuda_version"),
        "gpu": ", ".join(str(item) for item in gpu_names) if gpu_names else None,
        "vllm_version": vllm.get("version"),
        "lmcache_version": lmcache.get("version"),
        "connector_version": connector.get("version"),
        "bifrostd": bifrostd.get("version") or bifrostd.get("path"),
        "driver_version": torch.get("driver_version") or torch.get("cuda_driver_version"),
        "readiness": _readiness_text(
            fake=fake,
            gpu=gpu,
            full=full,
            legacy_real=legacy_real,
        ),
    }


def _scenario_summary(
    run_summary: dict[str, Any],
    comparison_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    backend = run_summary.get("backend")
    label = str(run_summary.get("label") or "")
    real_status = "skipped" if backend == "fake" or label.startswith("fake_") else "attempted"
    model, local_statement = _model_summary(run_summary, comparison_summary)
    return {
        "label": run_summary.get("label"),
        "backend": backend,
        "base_url": run_summary.get("base_url"),
        "endpoint": run_summary.get("endpoint"),
        "model": model,
        "model_local_asset_statement": local_statement,
        "run_duration_s": run_summary.get("run_duration_s"),
        "started_unix_s": run_summary.get("started_unix_s"),
        "ended_unix_s": run_summary.get("ended_unix_s"),
        "real_vllm_status": real_status,
        "connector_metrics_source": run_summary.get("connector_metrics_source"),
        "performance_metrics_source": run_summary.get("performance_metrics_source"),
    }


def _workload_summary(run_summary: dict[str, Any]) -> dict[str, Any]:
    workload = run_summary.get("workload_summary")
    workload = workload if isinstance(workload, dict) else {}
    return {
        "request_count": run_summary.get("request_count"),
        "success_count": run_summary.get("success_count"),
        "error_count": run_summary.get("error_count"),
        "cache_expected_request_count": run_summary.get("cache_expected_request_count"),
        "repeated_prefix_group_count": run_summary.get("repeated_prefix_group_count"),
        "workload_name": workload.get("workload_name"),
        "max_tokens_values": workload.get("max_tokens_values"),
        "source": run_summary.get("workload_path"),
    }


def _mode_summary(
    run_summary: dict[str, Any],
    comparison_summary: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if comparison_summary is not None:
        for item in comparison_summary.get("mode_results", []):
            summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
            rows.append(
                {
                    "mode": item.get("mode"),
                    "status": item.get("status"),
                    "request_count": summary.get("request_count"),
                    "p50_latency_ms": summary.get("p50_latency_ms"),
                    "p95_latency_ms": summary.get("p95_latency_ms"),
                    "p50_ttft_ms": summary.get("p50_ttft_ms"),
                    "error_rate": summary.get("error_rate"),
                    "notes": item.get("skip_reason") or item.get("error") or "",
                }
            )
        return rows
    return [
        {
            "mode": run_summary.get("label") or run_summary.get("backend") or "run",
            "status": "completed",
            "request_count": run_summary.get("request_count"),
            "p50_latency_ms": run_summary.get("p50_latency_ms"),
            "p95_latency_ms": run_summary.get("p95_latency_ms"),
            "p50_ttft_ms": run_summary.get("p50_ttft_ms"),
            "error_rate": run_summary.get("error_rate"),
            "notes": "",
        }
    ]


def _latency_summary(run_summary: dict[str, Any]) -> dict[str, Any]:
    ttft_count = run_summary.get("ttft_available_count")
    ttft_available = isinstance(ttft_count, int) and ttft_count > 0
    return {
        "p50_latency_ms": run_summary.get("p50_latency_ms"),
        "p95_latency_ms": run_summary.get("p95_latency_ms"),
        "mean_latency_ms": run_summary.get("mean_latency_ms"),
        "p50_ttft_ms": run_summary.get("p50_ttft_ms"),
        "p95_ttft_ms": run_summary.get("p95_ttft_ms"),
        "mean_ttft_ms": run_summary.get("mean_ttft_ms"),
        "ttft_available_count": ttft_count,
        "ttft_status": "available" if ttft_available else "unavailable",
        "ttft_note": (
            "TTFT was present in raw request metrics."
            if ttft_available
            else "TTFT was not present in this run's raw request metrics."
        ),
        "throughput_rps": run_summary.get("throughput_rps"),
        "error_rate": run_summary.get("error_rate"),
    }


def _bifrost_activity_summary(run_summary: dict[str, Any]) -> dict[str, Any]:
    bifrost_delta = run_summary.get("bifrost_stats_delta")
    connector_delta = run_summary.get("connector_metrics_delta")
    bifrost_delta = bifrost_delta if isinstance(bifrost_delta, dict) else {}
    connector_delta = connector_delta if isinstance(connector_delta, dict) else {}
    stats = run_summary.get("bifrost_stats") if isinstance(run_summary.get("bifrost_stats"), dict) else {}
    before = stats.get("before") if isinstance(stats.get("before"), dict) else {}
    after = stats.get("after") if isinstance(stats.get("after"), dict) else {}
    fsck_status = _first_value(after, before, keys=("fsck_status", "fsck", "fsck_clean"))
    put_count = _first_number(connector_delta, bifrost_delta, keys=("put_count", "puts"))
    get_count = _first_number(connector_delta, bifrost_delta, keys=("get_count", "gets"))
    exists_count = _first_number(connector_delta, bifrost_delta, keys=("exists_count", "has_count"))
    bytes_stored = _first_number(connector_delta, bifrost_delta, keys=("bytes_stored", "bytes_put", "total_logical_bytes"))
    bytes_loaded = _first_number(connector_delta, bifrost_delta, keys=("bytes_loaded", "bytes_get"))
    object_delta = _first_number(bifrost_delta, keys=("object_count", "objects"))
    available = bool(connector_delta or bifrost_delta)
    note = "BIFROST connector or store deltas were present." if available else _bifrost_unavailable_note(stats)
    return {
        "status": "available" if available else "unavailable",
        "put_count": put_count,
        "get_count": get_count,
        "exists_count": exists_count,
        "bytes_stored": bytes_stored,
        "bytes_loaded": bytes_loaded,
        "object_count_delta": object_delta,
        "fsck_status": fsck_status if fsck_status is not None else "unavailable",
        "note": note,
        "source": _source_from_delta(connector_delta, bifrost_delta),
        "synthetic_label": run_summary.get("performance_metrics_source")
        == MetricSource.SYNTHETIC_FAKE_SERVER.value,
    }


def _correctness_summary(
    run_summary: dict[str, Any],
    raw_requests: list[dict[str, Any]],
) -> dict[str, Any]:
    errors = [row for row in raw_requests if row.get("error")]
    notes = [
        f"Successful requests: {run_summary.get('success_count')} of {run_summary.get('request_count')}.",
        "Output comparison mode: deterministic raw response comparison was not configured; correctness is advisory for serving output text.",
    ]
    if errors:
        notes.append(f"Request errors were observed: {len(errors)}.")
    else:
        notes.append("No request errors were recorded in raw request metrics.")
    return {
        "status": "advisory" if not errors else "errors_observed",
        "error_count": len(errors),
        "notes": notes,
    }


def _skipped_components(
    run_summary: dict[str, Any],
    comparison_summary: dict[str, Any] | None,
) -> list[str]:
    skipped: list[str] = []
    if _scenario_summary(run_summary, comparison_summary)["real_vllm_status"] == "skipped":
        skipped.append("Real vLLM serving mode was skipped for this fake/backend run.")
    if _latency_summary(run_summary)["ttft_status"] == "unavailable":
        skipped.append("TTFT metrics were unavailable.")
    if _bifrost_activity_summary(run_summary)["status"] == "unavailable":
        skipped.append("BIFROST connector/store stats were unavailable.")
    if comparison_summary is not None:
        for item in comparison_summary.get("mode_results", []):
            if item.get("status") == "skipped":
                skipped.append(f"{item.get('mode')} skipped: {item.get('skip_reason')}")
    return skipped


def _limitations_and_notes(
    run_summary: dict[str, Any],
    comparison_summary: dict[str, Any] | None,
) -> list[str]:
    notes = [
        "Speedups are not inferred unless baseline and candidate metrics are both present in the comparison summary.",
        "BIFROST byte and operation counters are cache-path evidence, not LMCache hit-rate proof.",
    ]
    backend = run_summary.get("backend")
    if backend == "fake":
        notes.append("This run used the fake OpenAI-compatible serving backend, not real vLLM.")
    if comparison_summary is None:
        notes.append("No comparison summary was provided; baseline deltas are unavailable.")
    else:
        notes.extend(str(note) for note in comparison_summary.get("notes", []))
    return notes


def _comparison_summary(
    comparison_dir: Path | None,
    comparison_summary: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if comparison_dir is None or comparison_summary is None:
        return None
    return {
        "source": str(comparison_dir / "comparison_summary.json"),
        "mode_count": len(comparison_summary.get("mode_results", [])),
        "comparison_count": len(comparison_summary.get("comparisons", [])),
        "notes": comparison_summary.get("notes", []),
        "comparisons": comparison_summary.get("comparisons", []),
    }


def _read_comparison(comparison_dir: Path | None) -> dict[str, Any] | None:
    if comparison_dir is None:
        return None
    path = comparison_dir / "comparison_summary.json"
    if not path.exists():
        return None
    return _read_json(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ServingReportError(f"missing required artifact: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ServingReportError(f"expected JSON object in {path}")
    return data


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise ServingReportError(f"missing required artifact: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        data = json.loads(line)
        if not isinstance(data, dict):
            raise ServingReportError(f"expected JSON object at {path}:{line_number}")
        rows.append(data)
    return rows


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validate_config(config: ServingReportConfig) -> None:
    if config.format not in REPORT_FORMATS:
        raise ServingReportError(f"unsupported report format: {config.format}")
    if not config.run_dir.exists():
        raise ServingReportError(f"run directory does not exist: {config.run_dir}")
    if config.comparison_dir is not None and not config.comparison_dir.exists():
        raise ServingReportError(f"comparison directory does not exist: {config.comparison_dir}")


def _details(checks: dict[str, Any], name: str) -> dict[str, Any]:
    item = checks.get(name) if isinstance(checks, dict) else None
    if not isinstance(item, dict):
        return {}
    details = item.get("details")
    return details if isinstance(details, dict) else {}


def _model_summary(
    run_summary: dict[str, Any],
    comparison_summary: dict[str, Any] | None,
) -> tuple[str | None, str]:
    doctor = run_summary.get("environment_doctor")
    checks = doctor.get("checks", {}) if isinstance(doctor, dict) else {}
    model_check = checks.get("model") if isinstance(checks, dict) else None
    model_details = model_check.get("details") if isinstance(model_check, dict) else {}
    if not isinstance(model_details, dict):
        model_details = {}
    value = model_details.get("value")
    if value is None and comparison_summary is not None:
        value = comparison_summary.get("model")
    model = str(value) if value else None
    kind = model_details.get("kind")
    readable = model_details.get("readable")
    status = model_check.get("status") if isinstance(model_check, dict) else None
    if kind == "local_path" and readable is True and status == "ready":
        return model, "configured model resolved to a readable local path"
    if kind == "local_path" and readable is False:
        return model, "configured model path was local but not readable"
    if model:
        return model, "model value recorded; local asset availability was not verified in this run artifact"
    return None, "unavailable"


def _readiness_text(
    *,
    fake: Any,
    gpu: Any,
    full: Any,
    legacy_real: Any,
) -> str:
    full_status = full.get("status") if isinstance(full, dict) else None
    gpu_status = gpu.get("status") if isinstance(gpu, dict) else None
    legacy_real_status = legacy_real.get("status") if isinstance(legacy_real, dict) else None
    fake_status = fake.get("status") if isinstance(fake, dict) else None
    parts: list[str] = []
    if full_status:
        parts.append(f"full_benchmark_ready={full_status}")
    if gpu_status:
        parts.append(f"gpu_serving_ready={gpu_status}")
    if legacy_real_status and not parts:
        parts.append(f"real_serving_ready={legacy_real_status}")
    if fake_status:
        parts.append(f"fake_ci_ready={fake_status}")
    return ", ".join(parts) if parts else "unavailable"


def _first_number(*dicts: dict[str, Any], keys: tuple[str, ...]) -> int | float | None:
    for data in dicts:
        for key in keys:
            value = data.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return value
    return None


def _first_value(*dicts: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for data in dicts:
        for key in keys:
            if key in data:
                return data[key]
    return None


def _bifrost_unavailable_note(stats: dict[str, Any]) -> str:
    before = stats.get("before") if isinstance(stats.get("before"), dict) else {}
    after = stats.get("after") if isinstance(stats.get("after"), dict) else {}
    reason = after.get("reason") or before.get("reason")
    status = after.get("status") or before.get("status")
    if reason:
        return str(reason)
    if status:
        return f"BIFROST stats collection status was {status}."
    return "No BIFROST connector or store deltas were present."


def _source_row(group: str, source: Any, note: Any = "") -> dict[str, Any]:
    source_text = _normalize_metric_source(source)
    return {
        "group": group,
        "source": source_text,
        "status": "available" if source_text != MetricSource.UNAVAILABLE.value else "unavailable",
        "note": note,
    }


def _normalize_metric_source(source: Any) -> str:
    if not source:
        return MetricSource.UNAVAILABLE.value
    text = str(source)
    if text == "actual_bifrost_remote_connector":
        return MetricSource.BIFROST_CONNECTOR_METRICS.value
    if text in {item.value for item in MetricSource}:
        return text
    return text


def _source_from_delta(
    connector_delta: dict[str, Any],
    bifrost_delta: dict[str, Any],
) -> str:
    if connector_delta:
        return MetricSource.BIFROST_CONNECTOR_JSONL.value
    if bifrost_delta:
        return MetricSource.BIFROST_STORE_STATS.value
    return MetricSource.UNAVAILABLE.value


def _legacy_config_entries(run_dir: Path) -> list[dict[str, Any]]:
    entries = []
    for name in ("config.json", "resolved_run_config.yaml", *CONFIG_ARTIFACTS):
        path = run_dir / name
        if path.exists() and path.is_file():
            entries.append(artifact_entry(path, root=run_dir).to_dict())
    by_path = {entry["relative_path"]: entry for entry in entries}
    return [by_path[key] for key in sorted(by_path)]


def _common_config_equality(
    run_dir: Path,
    comparison_summary: dict[str, Any] | None,
) -> str:
    manifest_path = run_dir / "comparison_manifest.json"
    if manifest_path.exists():
        manifest = _read_json(manifest_path)
        fairness = manifest.get("fairness") if isinstance(manifest.get("fairness"), dict) else {}
        status = fairness.get("status")
        if status:
            return str(status)
    if comparison_summary is not None:
        fairness = comparison_summary.get("fairness")
        if isinstance(fairness, dict) and fairness.get("status"):
            return str(fairness["status"])
    return "unavailable"


def _fmt(value: Any) -> str:
    if value is None:
        return "unavailable"
    if value == "":
        return "unavailable"
    return str(value)


def _fmt_num(value: Any) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _md_code(value: Any) -> str:
    return f"`{_fmt(value)}`"


def _escape_pipes(value: str) -> str:
    return value.replace("|", "\\|")


__all__ = [
    "ServingReportConfig",
    "ServingReportError",
    "ServingReportResult",
    "build_report_summary",
    "generate_serving_report",
    "main",
    "render_markdown",
    "write_comparison_csv",
    "write_per_request_csv",
]
