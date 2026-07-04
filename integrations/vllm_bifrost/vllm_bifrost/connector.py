"""BIFROST Phase 7 vLLM KVTransfer connector skeleton."""

from __future__ import annotations

from typing import Any, Mapping

from .compat import compatibility_diagnostics, get_connector_base_class
from .config import BifrostVLLMConnectorConfig
from .errors import (
    ConnectorConfigurationError,
    ConnectorLifecycleError,
    UnsupportedOperationError,
)
from .metrics import ConnectorJsonlLogger, ConnectorMetrics, monotonic_ms

_IMPLEMENTED_ABSTRACT_METHODS = frozenset(
    (
        "build_connector_meta",
        "get_num_new_matched_tokens",
        "save_kv_layer",
        "start_load_kv",
        "update_state_after_alloc",
        "wait_for_layer_load",
        "wait_for_save",
    )
)
_vllm_base_class = get_connector_base_class()
_vllm_abstract_methods = frozenset(
    getattr(_vllm_base_class, "__abstractmethods__", frozenset())
    if _vllm_base_class is not None
    else frozenset()
)
_can_subclass_vllm_base = (
    _vllm_base_class is not None
    and _vllm_abstract_methods.issubset(_IMPLEMENTED_ABSTRACT_METHODS)
)
_BaseConnector = _vllm_base_class if _can_subclass_vllm_base else object


class BifrostKVConnector(_BaseConnector):  # type: ignore[misc, valid-type]
    """Dynamic-importable vLLM KVTransfer connector shell.

    Save and load are intentionally not implemented in this skeleton. Fake
    mode records calls and returns safe non-hit defaults. Real vLLM mode raises
    deterministic unsupported-operation errors for hooks that would otherwise
    imply functional KV transfer.
    """

    def __init__(
        self,
        vllm_config: object | None = None,
        role: object | None = None,
        *,
        config: BifrostVLLMConnectorConfig | Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._vllm_base_init_error: str | None = None
        if _can_subclass_vllm_base:
            self._try_base_init(vllm_config, role)

        self.vllm_config = vllm_config
        self.role = role
        self.vllm_kwargs = dict(kwargs)
        self.config = _parse_connector_config(config, vllm_config, kwargs)
        self.connector_instance_id = (
            self.config.connector_instance_id or "bifrost-vllm-connector"
        )
        self.closed = False
        self._kv_caches: object | None = None
        self._block_ids_with_load_errors: set[int] = set()
        self._call_history: list[dict[str, Any]] = []
        self._real_vllm_mode = _looks_like_real_vllm(vllm_config) or _looks_like_real_vllm(
            role
        )
        self._metrics = ConnectorMetrics()
        self._metrics.increment("init_count")
        self._jsonl_logger = ConnectorJsonlLogger(self.config.trace_jsonl_path)
        self._record_call("__init__", (), kwargs)
        self._emit_event("__init__", operation="lifecycle")

    @property
    def call_history(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._call_history]

    def register_kv_caches(self, kv_caches: object | None = None, *args: Any, **kwargs: Any) -> None:
        self._ensure_open("register_kv_caches")
        self._metrics.increment("register_kv_caches_count")
        self._record_call("register_kv_caches", (kv_caches, *args), kwargs)
        if self._real_vllm_mode:
            self._raise_unsupported("register_kv_caches")
        self._kv_caches = kv_caches
        self._emit_event("register_kv_caches", operation="lifecycle")
        return None

    def start_load_kv(self, forward_context: object | None = None, **kwargs: Any) -> None:
        self._ensure_open("start_load_kv")
        start_ms = monotonic_ms()
        self._metrics.increment("start_load_kv_count")
        self._record_call("start_load_kv", (forward_context,), kwargs)
        if self._real_vllm_mode:
            self._raise_unsupported("start_load_kv")
        reason_code = (
            "load_disabled"
            if self.config.load_mode == "disabled"
            else "load_not_implemented"
        )
        self._metrics.increment("load_skipped_count")
        if self.config.failure_policy == "recompute":
            self._metrics.increment("load_recompute_count")
        self._metrics.add_duration_ms("total_load_ms", monotonic_ms() - start_ms)
        self._emit_event(
            "start_load_kv",
            operation="load",
            reason_code=reason_code,
            duration_ms=monotonic_ms() - start_ms,
        )
        return None

    def wait_for_layer_load(self, layer_name: str | None = None, *args: Any, **kwargs: Any) -> None:
        self._ensure_open("wait_for_layer_load")
        self._metrics.increment("wait_for_layer_load_count")
        self._record_call("wait_for_layer_load", (layer_name, *args), kwargs)
        if self._real_vllm_mode:
            self._raise_unsupported("wait_for_layer_load")
        self._emit_event("wait_for_layer_load", operation="load")
        return None

    def save_kv_layer(
        self,
        layer_name: str | None = None,
        kv_layer: object | None = None,
        attn_metadata: object | None = None,
        **kwargs: Any,
    ) -> None:
        self._ensure_open("save_kv_layer")
        start_ms = monotonic_ms()
        self._metrics.increment("save_kv_layer_count")
        self._record_call("save_kv_layer", (layer_name, kv_layer, attn_metadata), kwargs)
        if self._real_vllm_mode:
            self._raise_unsupported("save_kv_layer")
        reason_code = (
            "save_disabled"
            if self.config.save_mode == "disabled"
            else "save_not_implemented"
        )
        self._metrics.increment("save_skipped_count")
        self._metrics.add_duration_ms("total_save_ms", monotonic_ms() - start_ms)
        self._emit_event(
            "save_kv_layer",
            operation="save",
            reason_code=reason_code,
            duration_ms=monotonic_ms() - start_ms,
        )
        return None

    def wait_for_save(self, *args: Any, **kwargs: Any) -> None:
        self._ensure_open("wait_for_save")
        self._metrics.increment("wait_for_save_count")
        self._record_call("wait_for_save", args, kwargs)
        if self._real_vllm_mode:
            self._raise_unsupported("wait_for_save")
        self._emit_event("wait_for_save", operation="save")
        return None

    def get_finished(
        self,
        finished_req_ids: set[str] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[None, None]:
        self._ensure_open("get_finished")
        self._metrics.increment("get_finished_count")
        self._record_call("get_finished", (finished_req_ids, *args), kwargs)
        self._emit_event("get_finished", operation="lifecycle")
        return (None, None)

    def get_block_ids_with_load_errors(self, *args: Any, **kwargs: Any) -> list[int]:
        self._ensure_open("get_block_ids_with_load_errors")
        self._metrics.increment("get_block_ids_with_load_errors_count")
        self._record_call("get_block_ids_with_load_errors", args, kwargs)
        self._emit_event("get_block_ids_with_load_errors", operation="load")
        return sorted(self._block_ids_with_load_errors)

    def shutdown(self, *args: Any, **kwargs: Any) -> None:
        if not self.closed:
            self._metrics.increment("shutdown_count")
        self._record_call("shutdown", args, kwargs)
        self.closed = True
        self._emit_event("shutdown", operation="lifecycle")
        return None

    def get_kv_connector_stats(self) -> dict[str, Any]:
        self._metrics.increment("get_kv_connector_stats_count")
        self._record_call("get_kv_connector_stats", (), {})
        stats = self._metrics.snapshot()
        stats.update(
            {
                "connector_instance_id": self.connector_instance_id,
                "closed": self.closed,
                "engine_name": self.config.engine_name,
                "integration_name": self.config.integration_name,
                "kv_cache_format": self.config.kv_cache_format,
                "save_mode": self.config.save_mode,
                "load_mode": self.config.load_mode,
                "failure_policy": self.config.failure_policy,
                "real_vllm_mode": self._real_vllm_mode,
                "vllm_base_subclassed": _can_subclass_vllm_base,
                "vllm_base_init_error": self._vllm_base_init_error,
            }
        )
        return stats

    def build_connector_meta(self, scheduler_output: object | None = None) -> object | None:
        self._ensure_open("build_connector_meta")
        self._record_call("build_connector_meta", (scheduler_output,), {})
        if self._real_vllm_mode:
            self._raise_unsupported("build_connector_meta")
        build = getattr(scheduler_output, "build_connector_metadata", None)
        if callable(build):
            return build(
                operation="load",
                connector_instance_id=self.connector_instance_id,
            )
        return None

    def get_num_new_matched_tokens(
        self,
        request: object | None = None,
        num_computed_tokens: int = 0,
    ) -> tuple[None, bool]:
        self._ensure_open("get_num_new_matched_tokens")
        self._record_call(
            "get_num_new_matched_tokens",
            (request, num_computed_tokens),
            {},
        )
        if self._real_vllm_mode:
            self._raise_unsupported("get_num_new_matched_tokens")
        return (None, False)

    def update_state_after_alloc(
        self,
        request: object | None = None,
        blocks: object | None = None,
        num_external_tokens: int = 0,
    ) -> None:
        self._ensure_open("update_state_after_alloc")
        self._record_call(
            "update_state_after_alloc",
            (request, blocks, num_external_tokens),
            {},
        )
        if self._real_vllm_mode:
            self._raise_unsupported("update_state_after_alloc")
        return None

    def request_finished(
        self,
        request: object | None = None,
        block_ids: list[int] | None = None,
    ) -> tuple[bool, None]:
        self._ensure_open("request_finished")
        self._record_call("request_finished", (request, block_ids), {})
        return (False, None)

    @classmethod
    def get_required_kvcache_layout(cls, vllm_config: object | None = None) -> None:
        return None

    @staticmethod
    def compatibility_diagnostics() -> dict[str, Any]:
        diagnostics = compatibility_diagnostics()
        diagnostics["vllm_base_subclassed"] = _can_subclass_vllm_base
        diagnostics["unsupported_base_abstract_methods"] = sorted(
            _vllm_abstract_methods - _IMPLEMENTED_ABSTRACT_METHODS
        )
        return diagnostics

    def _try_base_init(self, vllm_config: object | None, role: object | None) -> None:
        try:
            super().__init__(vllm_config, role)  # type: ignore[misc]
        except TypeError:
            try:
                super().__init__()  # type: ignore[misc]
            except Exception as exc:  # pragma: no cover - optional vLLM behavior.
                self._vllm_base_init_error = (
                    f"{type(exc).__name__}: vLLM connector base initialization "
                    f"failed: {exc}"
                )
        except Exception as exc:  # pragma: no cover - optional vLLM behavior.
            self._vllm_base_init_error = (
                f"{type(exc).__name__}: vLLM connector base initialization failed: {exc}"
            )

    def _ensure_open(self, method_name: str) -> None:
        if self.closed:
            self._metrics.increment("lifecycle_error_count")
            error = ConnectorLifecycleError(
                f"{method_name} called after connector shutdown",
                reason_code="connector_closed",
            )
            self._emit_event(
                method_name,
                operation="lifecycle",
                reason_code=error.reason_code,
            )
            raise error

    def _raise_unsupported(self, method_name: str) -> None:
        self._metrics.increment("unsupported_operation_count")
        error = UnsupportedOperationError(
            f"{method_name} is not implemented for real vLLM in the Phase 7 "
            "connector skeleton",
            reason_code=f"{method_name}_not_implemented",
        )
        self._emit_event(
            method_name,
            operation="lifecycle",
            reason_code=error.reason_code,
        )
        raise error

    def _record_call(
        self,
        method_name: str,
        args: tuple[Any, ...],
        kwargs: Mapping[str, Any],
    ) -> None:
        entry: dict[str, Any] = {
            "method": method_name,
            "arg_count": len(args),
            "kwarg_names": sorted(str(key) for key in kwargs),
            "closed": self.closed,
        }
        if args and isinstance(args[0], str):
            entry["first_arg"] = args[0]
        self._call_history.append(entry)

    def _emit_event(
        self,
        lifecycle_method: str,
        *,
        operation: str,
        reason_code: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        self._jsonl_logger.emit(
            "vllm_connector_lifecycle",
            operation=operation,
            connector_instance_id=self.connector_instance_id,
            lifecycle_method=lifecycle_method,
            reason_code=reason_code,
            duration_ms=duration_ms,
        )


def _parse_connector_config(
    config: BifrostVLLMConnectorConfig | Mapping[str, Any] | None,
    vllm_config: object | None,
    kwargs: Mapping[str, Any],
) -> BifrostVLLMConnectorConfig:
    if isinstance(config, BifrostVLLMConnectorConfig):
        return config
    if isinstance(config, Mapping):
        return BifrostVLLMConnectorConfig.from_dict(config)
    if config is not None:
        raise ConnectorConfigurationError("config must be a mapping or config object")
    if "kv_connector_extra_config" in kwargs:
        return BifrostVLLMConnectorConfig.from_dict(
            {"kv_connector_extra_config": kwargs["kv_connector_extra_config"]}
        )
    return BifrostVLLMConnectorConfig.from_vllm_config(vllm_config)


def _looks_like_real_vllm(value: object | None) -> bool:
    if value is None:
        return False
    module = type(value).__module__
    if module == "vllm" or module.startswith("vllm."):
        return True
    nested = getattr(value, "kv_transfer_config", None)
    nested_module = type(nested).__module__ if nested is not None else ""
    return nested_module == "vllm" or nested_module.startswith("vllm.")


__all__ = ["BifrostKVConnector"]
