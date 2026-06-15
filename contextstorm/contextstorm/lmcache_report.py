"""ContextStorm Phase 5 LMCache connector report generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .lmcache_metrics import (
    parse_lmcache_operation_metrics,
    summarize_lmcache_metrics,
)


def write_lmcache_report(run_dir: Path) -> tuple[Path, Path]:
    run_path = run_dir / "run.json"
    if not run_path.exists():
        raise FileNotFoundError(f"missing run artifact: {run_path}")
    run = json.loads(run_path.read_text())
    operations = run.get("operations", [])
    metrics = [parse_lmcache_operation_metrics(operation) for operation in operations]
    summary = summarize_lmcache_metrics(metrics)
    summary.update(
        {
            "scenario": run.get("scenario", {}).get("name"),
            "benchmark_kind": "lmcache",
            "lmcache": run.get("scenario", {}).get("lmcache", {}),
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
    correctness = summary.get("correctness", {})
    lines = [
        f"# ContextStorm LMCache Summary: {summary['scenario']}",
        "",
        "## LMCache Connector Summary",
        "",
        "| operations | successes | failures | skipped | objects | bytes_put | bytes_get | store_objects | fsck |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        "| {ops} | {success} | {failure} | {skipped} | {objects} | {bytes_put} | {bytes_get} | {store_objects} | {fsck} |".format(
            ops=summary.get("operation_count", 0),
            success=summary.get("success_count", 0),
            failure=summary.get("failure_count", 0),
            skipped=summary.get("skip_count", 0),
            objects=summary.get("object_count", 0),
            bytes_put=summary.get("bytes_put", 0),
            bytes_get=summary.get("bytes_get", 0),
            store_objects=summary.get("bifrost_store_object_count", 0),
            fsck=summary.get("fsck_status"),
        ),
        "",
        "## Correctness Checks",
        "",
        f"- all_fake_roundtrips_match: {correctness.get('all_fake_roundtrips_match')}",
        f"- exists_true_after_put: {correctness.get('exists_true_after_put')}",
        f"- missing_returns_none: {correctness.get('missing_returns_none')}",
        f"- fsck_clean: {correctness.get('fsck_clean')}",
        f"- batch_contains_match: {correctness.get('batch_contains_match')}",
        f"- batch_get_match: {correctness.get('batch_get_match')}",
        f"- corrupt_object_rejected: {correctness.get('corrupt_object_rejected')}",
        "",
        "## Timing Breakdown",
        "",
        "| put_ms | exists_ms | get_ms | list_ms | close_ms | serialization_ms | deserialization_ms | batch_put_ms | batch_contains_ms | batch_get_ms |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        "| {put:.3f} | {exists:.3f} | {get:.3f} | {list_:.3f} | {close:.3f} | {ser:.3f} | {deser:.3f} | {bput:.3f} | {bcontains:.3f} | {bget:.3f} |".format(
            put=float(summary.get("connector_put_ms") or 0.0),
            exists=float(summary.get("connector_exists_ms") or 0.0),
            get=float(summary.get("connector_get_ms") or 0.0),
            list_=float(summary.get("connector_list_ms") or 0.0),
            close=float(summary.get("connector_close_ms") or 0.0),
            ser=float(summary.get("serialization_ms") or 0.0),
            deser=float(summary.get("deserialization_ms") or 0.0),
            bput=float(summary.get("batched_put_ms") or 0.0),
            bcontains=float(summary.get("batched_contains_ms") or 0.0),
            bget=float(summary.get("batched_get_ms") or 0.0),
        ),
        "",
        "## Per-Operation Metrics",
        "",
        "| rep | op | success | skipped | objects | put_ms | exists_ms | get_ms | list_ms | bytes_put | bytes_get | matches | missing | reason |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for metric in summary.get("operations", []):
        lines.append(
            "| {rep} | {op} | {success} | {skipped} | {objects} | {put:.3f} | {exists:.3f} | {get:.3f} | {list_:.3f} | {bytes_put} | {bytes_get} | {matches} | {missing} | {reason} |".format(
                rep=metric.get("repetition", ""),
                op=metric.get("operation", ""),
                success=metric.get("success"),
                skipped=metric.get("skipped"),
                objects=metric.get("object_count", 0),
                put=float(metric.get("connector_put_ms") or 0.0),
                exists=float(metric.get("connector_exists_ms") or 0.0),
                get=float(metric.get("connector_get_ms") or 0.0),
                list_=float(metric.get("connector_list_ms") or 0.0),
                bytes_put=metric.get("bytes_put", 0),
                bytes_get=metric.get("bytes_get", 0),
                matches=metric.get("roundtrip_match_count", 0),
                missing=metric.get("missing_count", 0),
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
            "ContextStorm LMCache workloads use BIFROST opaque_engine_blob objects. The default fake scenarios are local, CPU-only, and do not require real LMCache, vLLM, GPU hardware, model downloads, internet access, Docker, Kubernetes, CUDA, cloud credentials, or root network mutation.",
        ]
    )
    return "\n".join(lines)
