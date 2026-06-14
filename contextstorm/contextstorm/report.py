"""ContextStorm run report generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_report(run_dir: Path) -> tuple[Path, Path]:
    run_path = run_dir / "run.json"
    if not run_path.exists():
        raise FileNotFoundError(f"missing run artifact: {run_path}")
    run = json.loads(run_path.read_text())
    if run.get("benchmark_kind") == "store":
        from .store_report import write_store_report

        return write_store_report(run_dir)
    if run.get("benchmark_kind") == "model":
        from .model_report import write_model_report

        return write_model_report(run_dir)
    operations = run.get("operations", [])
    metrics = [operation.get("metrics", {}) for operation in operations]
    summary = {
        "scenario": run.get("scenario", {}).get("name"),
        "operation_count": len(operations),
        "success_count": sum(1 for metric in metrics if metric.get("success")),
        "failure_count": sum(1 for metric in metrics if not metric.get("success")),
        "bytes_sent": sum(int(metric.get("bytes_sent") or 0) for metric in metrics),
        "bytes_received": sum(
            int(metric.get("bytes_received") or 0) for metric in metrics
        ),
        "chunks_sent": sum(int(metric.get("chunks_sent") or 0) for metric in metrics),
        "retries": sum(int(metric.get("retries") or 0) for metric in metrics),
        "timeouts": sum(int(metric.get("timeouts") or 0) for metric in metrics),
        "environment": run.get("environment", {}),
        "operations": metrics,
    }
    summary_json = run_dir / "summary.json"
    summary_md = run_dir / "summary.md"
    summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    summary_md.write_text(_markdown(summary, run) + "\n")
    return summary_json, summary_md


def _markdown(summary: dict[str, Any], run: dict[str, Any]) -> str:
    env = summary["environment"]
    lines = [
        f"# ContextStorm Summary: {summary['scenario']}",
        "",
        "## Overview",
        "",
        f"- Operations: {summary['operation_count']}",
        f"- Successes: {summary['success_count']}",
        f"- Failures: {summary['failure_count']}",
        f"- Bytes sent: {summary['bytes_sent']}",
        f"- Bytes received: {summary['bytes_received']}",
        f"- Chunks sent: {summary['chunks_sent']}",
        f"- Retries: {summary['retries']}",
        f"- Timeouts: {summary['timeouts']}",
        "",
        "## Per-Run Metrics",
        "",
        "| rep | op | success | duration_ms | MiB/s | sent | received | chunks | reason | verified | payload_match |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |",
    ]
    for operation in run.get("operations", []):
        metric = operation.get("metrics", {})
        lines.append(
            "| {rep} | {op} | {success} | {duration} | {throughput:.3f} | {sent} | {received} | {chunks} | {reason} | {verified} | {match} |".format(
                rep=metric.get("repetition", operation.get("repetition", "")),
                op=metric.get("operation", operation.get("operation", "")),
                success=metric.get("success"),
                duration=metric.get("transfer_duration_ms", 0),
                throughput=float(metric.get("effective_throughput_mib_s") or 0),
                sent=metric.get("bytes_sent", 0),
                received=metric.get("bytes_received", 0),
                chunks=metric.get("chunks_sent", 0),
                reason=metric.get("reason_code") or "",
                verified=metric.get("committed_object_verified"),
                match=metric.get("get_payload_matches_put_payload"),
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
            "ContextStorm is a local synthetic transport benchmark. It does not run GPU inference, LMCache, vLLM, QUIC, compression, or root-required network emulation.",
        ]
    )
    return "\n".join(lines)
