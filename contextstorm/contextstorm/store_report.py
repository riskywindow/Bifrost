"""ContextStorm store benchmark report generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .store_metrics import parse_store_operation_metrics, summarize_store_metrics


def write_store_report(run_dir: Path) -> tuple[Path, Path]:
    run_path = run_dir / "run.json"
    if not run_path.exists():
        raise FileNotFoundError(f"missing run artifact: {run_path}")
    run = json.loads(run_path.read_text())
    operations = run.get("operations", [])
    metrics = [parse_store_operation_metrics(operation) for operation in operations]
    summary = summarize_store_metrics(metrics)
    summary.update(
        {
            "scenario": run.get("scenario", {}).get("name"),
            "benchmark_kind": "store",
            "environment": run.get("environment", {}),
            "operations": metrics,
        }
    )
    summary_json = run_dir / "summary.json"
    summary_md = run_dir / "summary.md"
    summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    summary_md.write_text(_markdown(summary, run) + "\n")
    return summary_json, summary_md


def _markdown(summary: dict[str, Any], run: dict[str, Any]) -> str:
    env = summary["environment"]
    correctness = summary.get("correctness", {})
    lines = [
        f"# ContextStorm Store Summary: {summary['scenario']}",
        "",
        "## Overview",
        "",
        f"- Operations: {summary['operation_count']}",
        f"- Successes: {summary['success_count']}",
        f"- Failures: {summary['failure_count']}",
        f"- Objects inserted: {summary['objects_inserted']}",
        f"- Objects evicted: {summary['objects_evicted']}",
        f"- Bytes committed: {summary['bytes_committed']}",
        f"- Bytes evicted: {summary['bytes_evicted']}",
        f"- Memory tier hits: {summary['memory_tier_hits']}",
        f"- Memory tier misses: {summary['memory_tier_misses']}",
        "",
        "## Correctness Checks",
        "",
        f"- payload_roundtrip_match: {correctness.get('payload_roundtrip_match')}",
        f"- pinned_not_evicted: {correctness.get('pinned_not_evicted')}",
        f"- fsck_clean_after_run: {correctness.get('fsck_clean_after_run')}",
        f"- manifest_completeness_expected: {correctness.get('manifest_completeness_expected')}",
        "",
        "## Per-Operation Metrics",
        "",
        "| rep | op | success | put_ms | get_ms | has_ms | list_ms | query_ms | inspect_ms | evict_ms | fsck_ms | inserted | evicted | pinned | committed | freed | manifest | reason |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for operation in run.get("operations", []):
        metric = parse_store_operation_metrics(operation)
        lines.append(
            "| {rep} | {op} | {success} | {put} | {get} | {has} | {list_} | {query} | {inspect} | {evict} | {fsck} | {inserted} | {evicted} | {pinned} | {committed} | {freed} | {manifest} | {reason} |".format(
                rep=metric.get("repetition", ""),
                op=metric.get("operation", ""),
                success=metric.get("success"),
                put=metric.get("put_duration_ms", 0),
                get=metric.get("get_duration_ms", 0),
                has=metric.get("has_latency_ms", 0),
                list_=metric.get("list_latency_ms", 0),
                query=metric.get("query_latency_ms", 0),
                inspect=metric.get("inspect_latency_ms", 0),
                evict=metric.get("eviction_duration_ms", 0),
                fsck=metric.get("fsck_duration_ms", 0),
                inserted=metric.get("objects_inserted", 0),
                evicted=metric.get("objects_evicted", 0),
                pinned=metric.get("objects_pinned", 0),
                committed=metric.get("bytes_committed", 0),
                freed=metric.get("bytes_evicted", 0),
                manifest=metric.get("manifest_completeness"),
                reason=metric.get("reason_code") or "",
            )
        )
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
            "ContextStorm store benchmarks are local, CPU-only synthetic store workloads. They do not run GPU inference, LMCache, vLLM, QUIC, compression, RDMA, dashboards, or root-required network emulation.",
        ]
    )
    return "\n".join(lines)
