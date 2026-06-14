"""ContextStorm Phase 4 model correctness report generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model_metrics import (
    parse_model_operation_metrics,
    summarize_model_metrics,
)


def write_model_report(run_dir: Path) -> tuple[Path, Path]:
    run_path = run_dir / "run.json"
    if not run_path.exists():
        raise FileNotFoundError(f"missing run artifact: {run_path}")
    run = json.loads(run_path.read_text())
    operations = run.get("operations", [])
    metrics = [parse_model_operation_metrics(operation) for operation in operations]
    summary = summarize_model_metrics(metrics)
    summary.update(
        {
            "scenario": run.get("scenario", {}).get("name"),
            "benchmark_kind": "model",
            "model": run.get("scenario", {}).get("model", {}),
            "environment": run.get("environment", {}),
            "operations": metrics,
        }
    )
    summary_json = run_dir / "summary.json"
    summary_md = run_dir / "summary.md"
    summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    summary_md.write_text(_markdown(summary) + "\n")
    return summary_json, summary_md


def _markdown(summary: dict[str, Any]) -> str:
    env = summary["environment"]
    model = summary.get("model") or {}
    correctness = summary.get("correctness", {})
    lines = [
        f"# ContextStorm Model Summary: {summary['scenario']}",
        "",
        "## Model Summary",
        "",
        "| vocab | max_seq | layers | heads | kv_heads | head_dim | dtype | seed |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
        "| {vocab} | {max_seq} | {layers} | {heads} | {kv_heads} | {head_dim} | {dtype} | {seed} |".format(
            vocab=model.get("vocab_size", ""),
            max_seq=model.get("max_seq_len", ""),
            layers=model.get("num_layers", ""),
            heads=model.get("num_heads", ""),
            kv_heads=model.get("num_kv_heads", ""),
            head_dim=model.get("head_dim", ""),
            dtype=model.get("dtype", ""),
            seed=model.get("seed", ""),
        ),
        "",
        "## Correctness Status",
        "",
        f"- Operations: {summary['operation_count']}",
        f"- Successes: {summary['success_count']}",
        f"- Failures: {summary['failure_count']}",
        f"- continuation_match: {correctness.get('continuation_match')}",
        f"- logits_within_tolerance: {correctness.get('logits_within_tolerance')}",
        f"- manifest_complete: {correctness.get('manifest_complete')}",
        f"- logit_max_abs_error: {summary.get('logit_max_abs_error')}",
        "",
        "## Pages",
        "",
        f"- Page count: {summary.get('page_count', 0)}",
        f"- Total payload bytes: {summary.get('total_payload_bytes', 0)}",
        f"- Pages stored: {summary.get('pages_stored', 0)}",
        f"- Pages rehydrated: {summary.get('pages_rehydrated', 0)}",
        f"- Manifest completeness: {summary.get('manifest_completeness')}",
        "",
        "## Timing Breakdown",
        "",
        "| prefill_ms | serialize_ms | put_ms | get_ms | manifest_create_ms | manifest_check_ms | rehydrate_ms | decode_resume_ms |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        "| {prefill:.3f} | {serialize:.3f} | {put:.3f} | {get:.3f} | {manifest_create:.3f} | {manifest_check:.3f} | {rehydrate:.3f} | {decode_resume:.3f} |".format(
            prefill=float(summary.get("prefill_ms") or 0),
            serialize=float(summary.get("kv_page_serialize_ms") or 0),
            put=float(summary.get("store_put_ms") or 0),
            get=float(summary.get("store_get_ms") or 0),
            manifest_create=float(summary.get("manifest_create_ms") or 0),
            manifest_check=float(summary.get("manifest_check_ms") or 0),
            rehydrate=float(summary.get("rehydrate_ms") or 0),
            decode_resume=float(summary.get("decode_resume_ms") or 0),
        ),
        "",
        "## Per-Operation Metrics",
        "",
        "| rep | op | success | pages | bytes | put_ms | get_ms | rehydrate_ms | logit_error | continuation | manifest | reason |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for metric in summary.get("operations", []):
        lines.append(
            "| {rep} | {op} | {success} | {pages} | {bytes_} | {put:.3f} | {get:.3f} | {rehydrate:.3f} | {logit} | {continuation} | {manifest} | {reason} |".format(
                rep=metric.get("repetition", ""),
                op=metric.get("operation", ""),
                success=metric.get("success"),
                pages=metric.get("page_count", 0),
                bytes_=metric.get("total_payload_bytes", 0),
                put=float(metric.get("store_put_ms") or 0),
                get=float(metric.get("store_get_ms") or 0),
                rehydrate=float(metric.get("rehydrate_ms") or 0),
                logit=metric.get("logit_max_abs_error"),
                continuation=metric.get("continuation_match"),
                manifest=metric.get("manifest_completeness"),
                reason=metric.get("reason_code") or "",
            )
        )
    lines.extend(["", "## Failure Details", ""])
    failures = summary.get("failures") or []
    if failures:
        for failure in failures:
            lines.append(
                f"- {failure.get('operation')}: {failure.get('reason_code')} {failure.get('message', '')}"
            )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Environment",
            "",
            f"- Python: {env.get('python_version', '')}",
            f"- Platform: {env.get('platform', '')}",
            f"- Machine: {env.get('machine', '')}",
            f"- Processor: {env.get('processor', '')}",
            "",
            "## Notes",
            "",
            "ContextStorm model workloads are local CPU-only tiny-transformer correctness checks. They do not use GPU inference, production models, external downloads, LMCache, vLLM, dashboards, or root-required network emulation.",
        ]
    )
    return "\n".join(lines)
