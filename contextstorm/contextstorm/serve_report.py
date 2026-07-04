"""ContextStorm Phase 6 serving report generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .serve_metrics import parse_serving_operation_metrics, summarize_serving_metrics


def write_serving_report(run_dir: Path) -> tuple[Path, Path]:
    run_path = run_dir / "run.json"
    if not run_path.exists():
        raise FileNotFoundError(f"missing run artifact: {run_path}")
    run = json.loads(run_path.read_text(encoding="utf-8"))
    metrics = [
        parse_serving_operation_metrics(operation)
        for operation in run.get("operations", [])
    ]
    summary = summarize_serving_metrics(metrics)
    summary.update(
        {
            "scenario": run.get("scenario", {}).get("name"),
            "benchmark_kind": "serving",
            "environment": run.get("environment", {}),
            "operations": metrics,
            "phase6_reports": run.get("phase6_reports", []),
        }
    )
    summary_json = run_dir / "summary.json"
    summary_md = run_dir / "summary.md"
    summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    summary_md.write_text(_markdown(summary) + "\n")
    return summary_json, summary_md


def _markdown(summary: dict[str, Any]) -> str:
    serving = summary.get("serving", {})
    env = summary.get("environment", {})
    lines = [
        f"# ContextStorm Serving Summary: {summary.get('scenario')}",
        "",
        "## Serving Metrics",
        "",
        "| operations | successes | failures | skipped | requests | p50 latency ms | p95 latency ms | p50 TTFT ms | p95 TTFT ms | throughput rps | error rate | repeated prefix groups |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: |",
        "| {ops} | {success} | {failure} | {skipped} | {requests} | {p50} | {p95} | {ttft50} | {ttft95} | {rps} | {err} | {groups} |".format(
            ops=summary.get("operation_count", 0),
            success=summary.get("success_count", 0),
            failure=summary.get("failure_count", 0),
            skipped=summary.get("skip_count", 0),
            requests=summary.get("request_count", 0),
            p50=_fmt_num(serving.get("p50_latency_ms")),
            p95=_fmt_num(serving.get("p95_latency_ms")),
            ttft50=_fmt_num(serving.get("p50_ttft_ms")),
            ttft95=_fmt_num(serving.get("p95_ttft_ms")),
            rps=_fmt_num(serving.get("throughput_rps")),
            err=_fmt_num(serving.get("error_rate")),
            groups=_fmt_num(serving.get("repeated_prefix_group_count")),
        ),
        "",
        "## Correctness And Store Health",
        "",
        f"- Correctness status: {serving.get('correctness_status')}",
        f"- BIFROST stats delta: `{json.dumps(serving.get('bifrost_stats_delta'), sort_keys=True)}`",
        "",
        "## Raw Phase 6 Artifacts",
        "",
    ]
    artifacts = summary.get("raw_phase6_artifacts") or []
    if artifacts:
        for artifact in artifacts:
            for key, value in sorted(artifact.items()):
                lines.append(f"- {key}: `{value}`")
    else:
        lines.append("- None")
    lines.extend(["", "## Skipped Components", ""])
    skipped = summary.get("skipped_components") or []
    lines.extend(f"- {item}" for item in skipped) if skipped else lines.append("- None")
    lines.extend(["", "## Failures", ""])
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
            "",
            "## Notes",
            "",
            "ContextStorm serving scenarios are Phase 6 harness scenarios. The default fake serving scenario is local, CPU-only, and does not require vLLM, LMCache, GPU hardware, model downloads, internet access, Docker, Kubernetes, CUDA, cloud credentials, or root privileges.",
        ]
    )
    return "\n".join(lines)


def _fmt_num(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    if value is None:
        return "unavailable"
    return str(value)
