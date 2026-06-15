#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import contextlib
from dataclasses import asdict
import importlib
import io
import json
import os
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "bifrost_py"))
sys.path.insert(0, str(REPO_ROOT / "integrations" / "lmcache_bifrost"))

from bifrost_client import BifrostAsyncClient, BifrostClientConfig
from bifrost_client.errors import BifrostClientError


RUN_ENV = "BIFROST_RUN_VLLM_SMOKE"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Optional vLLM + LMCache + BIFROST smoke-test scaffold."
    )
    parser.add_argument(
        "--endpoint",
        default="127.0.0.1:7744",
        help="BIFROST daemon HOST:PORT",
    )
    parser.add_argument(
        "--config",
        default=str(
            REPO_ROOT
            / "examples"
            / "lmcache_bifrost"
            / "vllm_lmcache_bifrost_config.yaml"
        ),
        help="LMCache/vLLM configuration file path",
    )
    parser.add_argument(
        "--model",
        help="local model path; no downloads are attempted",
    )
    parser.add_argument(
        "--prompt",
        default="BIFROST LMCache smoke prompt. " * 4,
        help="prompt used twice when --run is enabled",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=8,
        help="small generation length for the optional request flow",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help=f"attempt vLLM requests; also requires {RUN_ENV}=1",
    )
    parser.add_argument("--json", action="store_true", help="print a JSON summary")
    args = parser.parse_args(argv)

    summary = asyncio.run(_run(args))
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(_format_human_summary(summary))

    if summary["status"] == "pass":
        return 0
    if summary["status"] in ("ready", "skipped", "not ready") and not args.run:
        return 0
    return 1


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    summary = _base_summary(args)
    _probe_imports(summary)
    summary["config_exists"] = Path(args.config).exists()
    summary["model_exists"] = _model_is_available(args.model)
    summary["bifrost"] = await _bifrost_stats(args.endpoint)

    readiness_errors = _readiness_errors(summary, args)
    summary["ready"] = not readiness_errors
    summary["skip_reason"] = "; ".join(readiness_errors) if readiness_errors else None

    if not args.run:
        summary["status"] = "ready" if summary["ready"] else "skipped"
        return summary

    if os.environ.get(RUN_ENV) != "1":
        summary["status"] = "skipped"
        summary["skip_reason"] = f"--run requires {RUN_ENV}=1"
        return summary
    if readiness_errors:
        summary["status"] = "not ready"
        return summary

    before = summary["bifrost"].get("stats")
    summary["bifrost_stats_before"] = before
    try:
        _run_vllm_requests(args)
    except Exception as exc:
        summary["status"] = "fail"
        summary["error"] = str(exc)
        summary["bifrost_stats_after"] = await _bifrost_stats(args.endpoint)
        summary["remote_put_increased"] = False
        summary["remote_get_increased"] = False
        return summary

    after_probe = await _bifrost_stats(args.endpoint)
    after = after_probe.get("stats")
    summary["bifrost_stats_after"] = after_probe
    summary["remote_put_increased"] = _increased(before, after, "object_count")
    summary["remote_get_increased"] = _increased(before, after, "total_access_count")
    summary["object_count_delta"] = _delta(before, after, "object_count")
    summary["remote_put_count_delta"] = summary["object_count_delta"]
    summary["remote_get_count_delta"] = _delta(before, after, "total_access_count")
    summary["total_access_count_delta"] = _delta(before, after, "total_access_count")
    summary["total_logical_bytes_delta"] = _delta(before, after, "total_logical_bytes")
    summary["memory_tier_hits_delta"] = _delta(before, after, "memory_tier_hits")
    summary["memory_tier_misses_delta"] = _delta(before, after, "memory_tier_misses")
    summary["status"] = "pass" if after_probe.get("ok") else "fail"
    return summary


def _base_summary(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "status": "not ready",
        "ready": False,
        "skip_reason": None,
        "endpoint": args.endpoint,
        "config": args.config,
        "model": args.model,
        "vllm_installed": False,
        "vllm_version": None,
        "lmcache_installed": False,
        "lmcache_version": None,
        "lmcache_bifrost_installed": False,
        "lmcache_bifrost_version": None,
        "config_exists": False,
        "model_exists": False,
        "bifrost": {"ok": False, "error": None, "stats": None},
        "remote_put_increased": False,
        "remote_get_increased": False,
        "remote_put_count_delta": None,
        "remote_get_count_delta": None,
        "total_logical_bytes_delta": None,
        "memory_tier_hits_delta": None,
        "memory_tier_misses_delta": None,
    }


def _probe_imports(summary: dict[str, Any]) -> None:
    forced_missing = {
        name.strip()
        for name in os.environ.get("BIFROST_VLLM_SMOKE_FORCE_MISSING", "").split(",")
        if name.strip()
    }
    vllm = _import_optional("vllm", forced_missing)
    lmcache_bifrost = _import_optional("lmcache_bifrost", forced_missing)
    lmcache_compat = _import_optional("lmcache_bifrost.lmcache_compat", forced_missing)
    summary["vllm_installed"] = vllm is not None
    summary["vllm_version"] = _version(vllm)
    has_lmcache = getattr(lmcache_compat, "has_lmcache", lambda: False)
    lmcache_version = getattr(lmcache_compat, "lmcache_version", lambda: None)
    summary["lmcache_installed"] = (
        False if "lmcache" in forced_missing else _quiet_call(has_lmcache, False)
    )
    summary["lmcache_version"] = (
        None if "lmcache" in forced_missing else _quiet_call(lmcache_version, None)
    )
    summary["lmcache_bifrost_installed"] = lmcache_bifrost is not None
    summary["lmcache_bifrost_version"] = _version(lmcache_bifrost)


def _import_optional(name: str, forced_missing: set[str]) -> object | None:
    if name in forced_missing:
        return None
    try:
        return _quiet_call(lambda: importlib.import_module(name), None)
    except Exception:
        return None


def _quiet_call(function: Any, default: Any) -> Any:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            return function()
    except Exception:
        return default


async def _bifrost_stats(endpoint: str) -> dict[str, Any]:
    client = BifrostAsyncClient(
        config=BifrostClientConfig(endpoint=endpoint, timeout_seconds=2.0)
    )
    try:
        stats = await client.stats()
        return {"ok": True, "error": None, "stats": asdict(stats)}
    except BifrostClientError as exc:
        return {"ok": False, "error": str(exc), "stats": None}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "stats": None}
    finally:
        await client.close()


def _readiness_errors(summary: dict[str, Any], args: argparse.Namespace) -> list[str]:
    errors: list[str] = []
    if not summary["vllm_installed"]:
        errors.append("vLLM is not installed")
    if not summary["lmcache_installed"]:
        errors.append("LMCache is not installed")
    if not summary["lmcache_bifrost_installed"]:
        errors.append("lmcache_bifrost is not importable")
    if not summary["config_exists"]:
        errors.append("configuration file is missing")
    if args.run and not summary["model_exists"]:
        errors.append("local model path is missing or --model was not provided")
    if not summary["bifrost"]["ok"]:
        errors.append("BIFROST daemon endpoint is not reachable")
    return errors


def _run_vllm_requests(args: argparse.Namespace) -> None:
    os.environ.setdefault("LMCACHE_CONFIG_FILE", str(Path(args.config).resolve()))
    os.environ.setdefault("VLLM_ALLOW_RUNTIME_LORA_UPDATING", "0")
    vllm = importlib.import_module("vllm")
    sampling_params_cls = getattr(vllm, "SamplingParams")
    llm_cls = getattr(vllm, "LLM")
    sampling_params = sampling_params_cls(temperature=0.0, max_tokens=args.max_tokens)
    llm = llm_cls(
        model=args.model,
        trust_remote_code=False,
        download_dir=None,
        enforce_eager=True,
    )
    prompts = [args.prompt, args.prompt]
    llm.generate(prompts, sampling_params)


def _model_is_available(model: str | None) -> bool:
    if not model:
        return False
    path = Path(model)
    return path.exists()


def _version(module: object | None) -> str | None:
    if module is None:
        return None
    value = getattr(module, "__version__", None)
    return str(value) if value is not None else None


def _increased(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    key: str,
) -> bool:
    delta = _delta(before, after, key)
    return delta is not None and delta > 0


def _delta(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    key: str,
) -> int | None:
    if before is None or after is None:
        return None
    if key not in before or key not in after:
        return None
    return int(after[key]) - int(before[key])


def _format_human_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"status: {summary['status']}",
        f"ready: {summary['ready']}",
        f"vllm installed: {summary['vllm_installed']}",
        f"vllm version: {summary['vllm_version'] or 'unknown'}",
        f"lmcache installed: {summary['lmcache_installed']}",
        f"lmcache version: {summary['lmcache_version'] or 'unknown'}",
        f"lmcache_bifrost importable: {summary['lmcache_bifrost_installed']}",
        f"bifrost endpoint reachable: {summary['bifrost']['ok']}",
        f"config exists: {summary['config_exists']}",
        f"model exists: {summary['model_exists']}",
        f"remote put increased: {summary['remote_put_increased']}",
        f"remote get increased: {summary['remote_get_increased']}",
    ]
    if summary.get("skip_reason"):
        lines.append(f"skip reason: {summary['skip_reason']}")
    if summary.get("error"):
        lines.append(f"error: {summary['error']}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
