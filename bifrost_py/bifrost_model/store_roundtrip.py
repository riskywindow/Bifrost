from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import torch

from bifrost_kv.validate import validate_object
from bifrost_model.config import TinyTransformerConfig
from bifrost_model.kv_cache import resume_generate_greedy
from bifrost_model.kv_page_codec import (
    NativePage,
    kv_cache_to_native_pages,
    native_pages_to_kv_cache,
)
from bifrost_model.tiny_transformer import TinyTransformer
from bifrost_model.tokenizer import TinyIntTokenizer


LOGIT_ATOL = 1e-6
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class PreparedStoreRoundtrip:
    config: TinyTransformerConfig
    model: TinyTransformer
    prompt_tokens: list[int]
    decode_tokens: int
    block_size: int
    baseline_continuation: list[int]
    next_input_id: int
    baseline_next_logits: torch.Tensor
    pages: list[NativePage]


@dataclass(frozen=True)
class PageFileSet:
    object_id: str
    page_dir: Path
    meta_path: Path
    payload_path: Path
    target_path: Path


@dataclass(frozen=True)
class StoreCommandTotals:
    success_count: int
    elapsed_ms: float


def run_store_roundtrip(
    *,
    endpoint: str,
    prompt: str,
    decode_tokens: int,
    block_size: int,
    seed: int,
    work_dir: str | Path | None = None,
    xfer_bin: str | Path | None = None,
    store_bin: str | Path | None = None,
) -> dict[str, Any]:
    if not endpoint:
        raise ValueError("--endpoint must be non-empty")

    with _managed_work_dir(work_dir) as root:
        prepared = prepare_store_roundtrip(
            prompt=prompt,
            decode_tokens=decode_tokens,
            block_size=block_size,
            seed=seed,
        )
        page_files = write_native_page_files(prepared.pages, root)
        put_totals = put_native_pages(
            endpoint=endpoint,
            page_files=page_files,
            xfer_bin=xfer_bin,
        )
        inspect_native_pages(
            endpoint=endpoint,
            object_ids=[page.object_id for page in page_files],
            store_bin=store_bin,
        )
        fetched_pages, get_totals = get_native_pages(
            endpoint=endpoint,
            expected_pages=prepared.pages,
            page_files=page_files,
            work_dir=root,
            xfer_bin=xfer_bin,
        )
        completion = complete_store_roundtrip(prepared, fetched_pages)

    object_ids = [page.metadata["object_id"] for page in prepared.pages]
    status = (
        "pass"
        if put_totals.success_count == len(object_ids)
        and get_totals.success_count == len(object_ids)
        and completion["continuation_match"]
        and completion["logit_max_abs_error"] <= LOGIT_ATOL
        else "fail"
    )
    return {
        "status": status,
        "prompt_tokens": prepared.prompt_tokens,
        "page_count": len(prepared.pages),
        "put_success_count": put_totals.success_count,
        "get_success_count": get_totals.success_count,
        "object_ids": object_ids,
        "baseline_continuation": prepared.baseline_continuation,
        "rehydrated_continuation": completion["rehydrated_continuation"],
        "continuation_match": completion["continuation_match"],
        "logit_max_abs_error": completion["logit_max_abs_error"],
        "total_put_ms": put_totals.elapsed_ms,
        "total_get_ms": get_totals.elapsed_ms,
        "rehydrate_ms": completion["rehydrate_ms"],
    }


def prepare_store_roundtrip(
    *,
    prompt: str,
    decode_tokens: int,
    block_size: int,
    seed: int,
) -> PreparedStoreRoundtrip:
    if decode_tokens < 0:
        raise ValueError("--decode-tokens must be non-negative")
    if block_size <= 0:
        raise ValueError("--block-size must be positive")

    config = TinyTransformerConfig(seed=seed)
    tokenizer = TinyIntTokenizer(vocab_size=config.vocab_size)
    prompt_tokens = tokenizer.encode(prompt)
    if not prompt_tokens:
        raise ValueError("--prompt must contain at least one integer token")

    input_ids = torch.tensor(prompt_tokens, dtype=torch.long)
    model = TinyTransformer(config)
    model.eval()

    with torch.no_grad():
        baseline_tokens = model.generate_greedy(input_ids, max_new_tokens=decode_tokens)
        baseline_continuation = baseline_tokens[len(prompt_tokens) :].tolist()
        prefix_logits, past_key_values = model.prefill(input_ids)
        pages = kv_cache_to_native_pages(
            past_key_values,
            model,
            tokenizer,
            config,
            prompt_tokens,
            block_size,
        )
        next_input_id = int(torch.argmax(prefix_logits[-1]).item())
        baseline_next_logits, _ = model.decode_one(next_input_id, past_key_values)

    return PreparedStoreRoundtrip(
        config=config,
        model=model,
        prompt_tokens=prompt_tokens,
        decode_tokens=decode_tokens,
        block_size=block_size,
        baseline_continuation=baseline_continuation,
        next_input_id=next_input_id,
        baseline_next_logits=baseline_next_logits.detach().clone(),
        pages=pages,
    )


def write_native_page_files(
    pages: Sequence[NativePage],
    work_dir: str | Path,
) -> list[PageFileSet]:
    root = Path(work_dir)
    root.mkdir(parents=True, exist_ok=True)
    files: list[PageFileSet] = []
    for index, page in enumerate(pages):
        object_id = page.metadata["object_id"]
        page_dir = root / f"page-{index:04d}"
        page_dir.mkdir(parents=True, exist_ok=True)
        meta_path = page_dir / "meta.json"
        payload_path = page_dir / "payload.bin"
        target_path = page_dir / "target.json"
        _write_json(meta_path, page.metadata)
        payload_path.write_bytes(page.payload)
        _write_json(target_path, page.target_profile)
        files.append(
            PageFileSet(
                object_id=object_id,
                page_dir=page_dir,
                meta_path=meta_path,
                payload_path=payload_path,
                target_path=target_path,
            )
        )
    return files


def put_native_pages(
    *,
    endpoint: str,
    page_files: Sequence[PageFileSet],
    xfer_bin: str | Path | None = None,
) -> StoreCommandTotals:
    xfer = _resolve_binary("bifrost-xfer", xfer_bin)
    success_count = 0
    total_ms = 0.0
    for page in page_files:
        elapsed_ms, result = _run_json_command(
            [
                str(xfer),
                "--json",
                "put",
                "--endpoint",
                endpoint,
                "--meta",
                str(page.meta_path),
                "--payload",
                str(page.payload_path),
                "--target",
                str(page.target_path),
            ]
        )
        total_ms += elapsed_ms
        if result.get("accepted") is not True:
            raise RuntimeError(
                f"PUT rejected {page.object_id}: {result.get('reason', 'unknown')}"
            )
        if result.get("object_id") != page.object_id:
            raise RuntimeError(f"PUT object_id mismatch for {page.object_id}")
        success_count += 1
    return StoreCommandTotals(success_count=success_count, elapsed_ms=total_ms)


def inspect_native_pages(
    *,
    endpoint: str,
    object_ids: Sequence[str],
    store_bin: str | Path | None = None,
) -> None:
    store = _resolve_binary("bifrost-store", store_bin)
    for object_id in object_ids:
        _, result = _run_json_command(
            [
                str(store),
                "inspect",
                "--endpoint",
                endpoint,
                "--object-id",
                object_id,
                "--json",
            ]
        )
        if result.get("found") is not True:
            raise RuntimeError(
                f"store inspect miss for {object_id}: {result.get('reason', 'unknown')}"
            )
        if result.get("servable") is not True or result.get("files_present") is not True:
            raise RuntimeError(f"store inspect found unservable object {object_id}")


def get_native_pages(
    *,
    endpoint: str,
    expected_pages: Sequence[NativePage],
    page_files: Sequence[PageFileSet],
    work_dir: str | Path,
    xfer_bin: str | Path | None = None,
) -> tuple[list[NativePage], StoreCommandTotals]:
    xfer = _resolve_binary("bifrost-xfer", xfer_bin)
    expected_by_id = {page.metadata["object_id"]: page for page in expected_pages}
    fetched: list[NativePage] = []
    success_count = 0
    total_ms = 0.0
    get_root = Path(work_dir) / "get"
    get_root.mkdir(parents=True, exist_ok=True)
    for index, page in enumerate(page_files):
        out_dir = get_root / f"page-{index:04d}"
        elapsed_ms, result = _run_json_command(
            [
                str(xfer),
                "--json",
                "get",
                "--endpoint",
                endpoint,
                "--object-id",
                page.object_id,
                "--out",
                str(out_dir),
            ]
        )
        total_ms += elapsed_ms
        if result.get("found") is not True:
            raise RuntimeError(
                f"GET miss for {page.object_id}: {result.get('reason', 'unknown')}"
            )
        if result.get("object_id") != page.object_id:
            raise RuntimeError(f"GET object_id mismatch for {page.object_id}")
        metadata = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
        payload = (out_dir / "payload.bin").read_bytes()
        expected = expected_by_id[page.object_id]
        validation = validate_object(metadata, payload, expected.target_profile)
        if validation.status != "accepted":
            raise RuntimeError(
                f"GET payload validation rejected {page.object_id}: "
                f"{validation.reason_code}"
            )
        fetched.append(
            NativePage(
                metadata=metadata,
                payload=payload,
                target_profile=expected.target_profile,
            )
        )
        success_count += 1
    return fetched, StoreCommandTotals(success_count=success_count, elapsed_ms=total_ms)


def complete_store_roundtrip(
    prepared: PreparedStoreRoundtrip,
    fetched_pages: Sequence[NativePage],
) -> dict[str, Any]:
    started = time.perf_counter()
    rehydrated = native_pages_to_kv_cache(fetched_pages, prepared.config)
    rehydrate_ms = (time.perf_counter() - started) * 1000.0

    with torch.no_grad():
        rehydrated_next_logits, _ = prepared.model.decode_one(
            prepared.next_input_id,
            rehydrated,
        )
        logit_max_abs_error = float(
            torch.max(
                torch.abs(prepared.baseline_next_logits - rehydrated_next_logits)
            ).item()
        )
        rehydrated_continuation = resume_generate_greedy(
            prepared.model,
            prepared.next_input_id,
            rehydrated,
            max_new_tokens=prepared.decode_tokens,
        ).tolist()

    return {
        "rehydrated_continuation": rehydrated_continuation,
        "continuation_match": prepared.baseline_continuation == rehydrated_continuation,
        "logit_max_abs_error": logit_max_abs_error,
        "rehydrate_ms": rehydrate_ms,
    }


def _resolve_binary(name: str, explicit: str | Path | None) -> Path:
    if explicit is not None:
        path = Path(explicit)
        if not path.exists():
            raise FileNotFoundError(f"{name} binary not found: {path}")
        return path

    found = shutil.which(name)
    if found:
        return Path(found)

    candidate = REPO_ROOT / "bifrostd" / "target" / "debug" / name
    if candidate.exists():
        return candidate

    raise FileNotFoundError(
        f"{name} binary not found; run `cargo build --manifest-path "
        "bifrostd/Cargo.toml --bins`"
    )


def _run_json_command(argv: list[str]) -> tuple[float, dict[str, Any]]:
    started = time.perf_counter()
    completed = subprocess.run(
        argv,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(argv)}\n"
            f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"command did not return JSON: {' '.join(argv)}\n"
            f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
        ) from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"command returned non-object JSON: {' '.join(argv)}")
    return elapsed_ms, value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _managed_work_dir(work_dir: str | Path | None):
    if work_dir is not None:
        path = Path(work_dir)
        path.mkdir(parents=True, exist_ok=True)
        return _ExistingWorkDir(path)
    return tempfile.TemporaryDirectory(prefix="bifrost-store-kv-")


class _ExistingWorkDir:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None
