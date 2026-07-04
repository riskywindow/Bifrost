"""vLLM KVTransfer API surface inspector for BIFROST Phase 7.

The inspector is intentionally read-only. It imports optional vLLM modules when
present, inspects signatures and configuration fields, and reports missing or
incompatible surfaces as structured data. It must not start vLLM serving,
download models, import LMCache, or initialize BIFROST connector behavior.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import importlib
import importlib.metadata
import inspect
import io
import json
import os
import pkgutil
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

STATUS_NOT_INSTALLED = "not_installed"
STATUS_INSTALLED = "installed"
STATUS_PARTIAL = "partial"
STATUS_ERROR = "error"

SCHEMA_VERSION = "bifrost.vllm_api_surface_inspection.v1"
VLLM_DISTRIBUTIONS = ("vllm",)
VLLM_CONFIG_MODULE = "vllm.config"
KV_TRANSFER_MODULE = "vllm.distributed.kv_transfer"
KV_CONNECTOR_V1_PACKAGE = "vllm.distributed.kv_transfer.kv_connector.v1"
KV_CONNECTOR_BASE_MODULE = (
    "vllm.distributed.kv_transfer.kv_connector.v1.base"
)
KV_CONNECTOR_BASE_NAME = "KVConnectorBase_V1"

EXPECTED_CONFIG_FIELDS = (
    "kv_connector",
    "kv_connector_module_path",
    "kv_connector_extra_config",
    "kv_role",
    "kv_rank",
    "kv_parallel_size",
    "kv_ip",
    "kv_port",
    "kv_load_failure_policy",
    "engine_id",
    "kv_buffer_device",
    "kv_buffer_size",
)

EXPECTED_CONNECTOR_METHODS = (
    "__init__",
    "register_kv_caches",
    "register_cross_layers_kv_cache",
    "start_load_kv",
    "wait_for_layer_load",
    "save_kv_layer",
    "wait_for_save",
    "get_finished",
    "get_block_ids_with_load_errors",
    "get_num_new_matched_tokens",
    "update_state_after_alloc",
    "build_connector_meta",
    "request_finished",
    "shutdown",
    "get_kv_connector_stats",
)

_OPTION_RE = re.compile(r"(?<![\w-])(--[A-Za-z][A-Za-z0-9-]*)")
_ADDRESS_RE = re.compile(r" at 0x[0-9a-fA-F]+")


@dataclass(frozen=True, slots=True)
class ImportAttempt:
    name: str
    imported: bool
    module: ModuleType | None = None
    error: str | None = None
    error_type: str | None = None
    module_path: str | None = None
    version: str | None = None
    import_stdout: str = ""
    import_stderr: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "imported": self.imported,
            "module": self.name,
            "module_path": self.module_path,
            "version": self.version,
        }
        if self.error:
            data["error"] = self.error
        if self.error_type:
            data["error_type"] = self.error_type
        if self.import_stdout:
            data["import_stdout"] = self.import_stdout[-2000:]
        if self.import_stderr:
            data["import_stderr"] = self.import_stderr[-2000:]
        return data


def has_vllm() -> bool:
    """Return True when ``import vllm`` succeeds in the current process."""

    return _import_module("vllm").imported


def vllm_version() -> str | None:
    """Return the importable or installed vLLM version, if discoverable."""

    attempt = _import_module("vllm")
    if attempt.imported and attempt.version:
        return attempt.version
    return _metadata_version(VLLM_DISTRIBUTIONS)


def inspect_kv_transfer_config() -> dict[str, Any]:
    """Inspect ``vllm.config.KVTransferConfig`` when it is available."""

    module_attempt = _import_module(VLLM_CONFIG_MODULE)
    imports = {VLLM_CONFIG_MODULE: module_attempt.to_dict()}
    fields = _empty_expected_field_map()
    result: dict[str, Any] = {
        "available": False,
        "module": VLLM_CONFIG_MODULE,
        "class_name": "KVTransferConfig",
        "imports": imports,
        "signature": None,
        "signature_parameters": [],
        "annotations": {},
        "dataclass_fields": {},
        "fields": fields,
        "expected_fields": {
            name: fields[name]["present"] for name in EXPECTED_CONFIG_FIELDS
        },
        "missing_expected_fields": list(EXPECTED_CONFIG_FIELDS),
        "warnings": [],
        "unsupported_reasons": [],
    }
    if not module_attempt.imported or module_attempt.module is None:
        result["unsupported_reasons"].append(
            "vllm.config is not importable; KVTransferConfig cannot be inspected."
        )
        return result

    config_cls = getattr(module_attempt.module, "KVTransferConfig", None)
    if config_cls is None:
        result["unsupported_reasons"].append(
            "vllm.config.KVTransferConfig is missing."
        )
        return result

    signature = _signature_details(config_cls)
    annotations = _annotation_details(config_cls)
    dataclass_fields = _dataclass_field_details(config_cls)
    fields = _collect_config_fields(signature, annotations, dataclass_fields)
    result.update(
        {
            "available": True,
            "class_module": str(getattr(config_cls, "__module__", "")),
            "class_qualname": str(getattr(config_cls, "__qualname__", "")),
            "signature": signature["signature"],
            "signature_parameters": signature["parameters"],
            "return_annotation": signature["return_annotation"],
            "annotations": annotations,
            "dataclass_fields": dataclass_fields,
            "fields": fields,
            "expected_fields": {
                name: fields[name]["present"] for name in EXPECTED_CONFIG_FIELDS
            },
            "missing_expected_fields": [
                name for name in EXPECTED_CONFIG_FIELDS if not fields[name]["present"]
            ],
        }
    )
    return result


def inspect_kv_connector_base_v1() -> dict[str, Any]:
    """Inspect the vLLM V1 KV connector base class when available."""

    module_attempt = _import_module(KV_CONNECTOR_BASE_MODULE)
    imports = {KV_CONNECTOR_BASE_MODULE: module_attempt.to_dict()}
    methods = _empty_expected_method_map()
    result: dict[str, Any] = {
        "available": False,
        "module": KV_CONNECTOR_BASE_MODULE,
        "class_name": KV_CONNECTOR_BASE_NAME,
        "imports": imports,
        "methods": methods,
        "public_method_names": [],
        "abstract_methods": [],
        "warnings": [],
        "unsupported_reasons": [],
    }
    if not module_attempt.imported or module_attempt.module is None:
        result["unsupported_reasons"].append(
            f"{KV_CONNECTOR_BASE_MODULE} is not importable."
        )
        return result

    base_cls = getattr(module_attempt.module, KV_CONNECTOR_BASE_NAME, None)
    if base_cls is None:
        result["unsupported_reasons"].append(
            f"{KV_CONNECTOR_BASE_MODULE}.{KV_CONNECTOR_BASE_NAME} is missing."
        )
        return result

    methods = _connector_method_details(base_cls)
    result.update(
        {
            "available": True,
            "class_module": str(getattr(base_cls, "__module__", "")),
            "class_qualname": str(getattr(base_cls, "__qualname__", "")),
            "methods": methods,
            "public_method_names": [
                name
                for name, details in methods.items()
                if details.get("present")
            ],
            "abstract_methods": sorted(
                name
                for name, details in methods.items()
                if details.get("present") and details.get("abstract")
            ),
        }
    )
    return result


def inspect_dynamic_connector_support() -> dict[str, Any]:
    """Inspect config and CLI signals for dynamic vLLM connector loading."""

    config = inspect_kv_transfer_config()
    kv_transfer_attempt = _import_module(KV_TRANSFER_MODULE)
    imports = dict(config.get("imports", {}))
    imports[KV_TRANSFER_MODULE] = kv_transfer_attempt.to_dict()

    expected = config.get("expected_fields", {})
    signals = {
        "kv_transfer_module_importable": kv_transfer_attempt.imported,
        "kv_connector_field": bool(expected.get("kv_connector")),
        "kv_connector_module_path_field": bool(
            expected.get("kv_connector_module_path")
        ),
        "kv_connector_extra_config_field": bool(
            expected.get("kv_connector_extra_config")
        ),
    }
    cli = _inspect_vllm_cli_flags(enabled=kv_transfer_attempt.imported)
    signals["serve_cli_has_kv_transfer_config_flag"] = bool(
        cli.get("has_kv_transfer_config_flag")
    )

    unsupported_reasons: list[str] = []
    if not signals["kv_transfer_module_importable"]:
        unsupported_reasons.append("vLLM KVTransfer package is not importable.")
    if not signals["kv_connector_field"]:
        unsupported_reasons.append("KVTransferConfig lacks kv_connector.")
    if not signals["kv_connector_module_path_field"]:
        unsupported_reasons.append(
            "KVTransferConfig lacks kv_connector_module_path for dynamic imports."
        )
    if not signals["kv_connector_extra_config_field"]:
        unsupported_reasons.append(
            "KVTransferConfig lacks kv_connector_extra_config for BIFROST settings."
        )

    supported = (
        signals["kv_transfer_module_importable"]
        and signals["kv_connector_field"]
        and signals["kv_connector_module_path_field"]
        and signals["kv_connector_extra_config_field"]
    )
    warnings: list[str] = []
    if cli.get("checked") and not cli.get("has_kv_transfer_config_flag"):
        warnings.append(
            "vLLM serve --help did not expose --kv-transfer-config; real smoke "
            "tests should remain skipped until a compatible CLI path is confirmed."
        )

    return {
        "supported": supported,
        "imports": imports,
        "signals": signals,
        "cli": cli,
        "warnings": warnings,
        "unsupported_reasons": unsupported_reasons,
    }


def inspect_available_kv_connector_modules() -> dict[str, Any]:
    """List connector classes in vLLM's KV connector V1 package, if visible."""

    package_attempt = _import_module(KV_CONNECTOR_V1_PACKAGE)
    base_attempt = _import_module(KV_CONNECTOR_BASE_MODULE)
    imports = {
        KV_CONNECTOR_V1_PACKAGE: package_attempt.to_dict(),
        KV_CONNECTOR_BASE_MODULE: base_attempt.to_dict(),
    }
    result: dict[str, Any] = {
        "available": False,
        "package": KV_CONNECTOR_V1_PACKAGE,
        "imports": imports,
        "modules": [],
        "connector_classes": [],
        "warnings": [],
        "unsupported_reasons": [],
    }
    if not package_attempt.imported or package_attempt.module is None:
        result["unsupported_reasons"].append(
            f"{KV_CONNECTOR_V1_PACKAGE} is not importable."
        )
        return result

    base_cls = (
        getattr(base_attempt.module, KV_CONNECTOR_BASE_NAME, None)
        if base_attempt.module is not None
        else None
    )
    package = package_attempt.module
    package_path = getattr(package, "__path__", None)
    if package_path is None:
        result["warnings"].append(
            f"{KV_CONNECTOR_V1_PACKAGE} has no __path__; submodules cannot be listed."
        )
        return result

    modules: list[dict[str, Any]] = []
    connector_classes: list[dict[str, Any]] = []
    for module_info in sorted(
        pkgutil.iter_modules(package_path, prefix=f"{KV_CONNECTOR_V1_PACKAGE}."),
        key=lambda item: item.name,
    ):
        module_attempt = _import_module(module_info.name)
        imports[module_info.name] = module_attempt.to_dict()
        module_record: dict[str, Any] = {
            "name": module_info.name,
            "is_package": module_info.ispkg,
            "imported": module_attempt.imported,
            "classes": [],
        }
        if module_attempt.imported and module_attempt.module is not None:
            classes = _discover_connector_classes(module_attempt.module, base_cls)
            module_record["classes"] = [item["name"] for item in classes]
            connector_classes.extend(classes)
        else:
            module_record["error"] = module_attempt.error
        modules.append(module_record)

    result.update(
        {
            "available": True,
            "modules": modules,
            "connector_classes": sorted(
                connector_classes,
                key=lambda item: (item["module"], item["name"]),
            ),
            "imports": imports,
        }
    )
    return result


def inspect_result() -> dict[str, Any]:
    """Return the full Phase 7 vLLM API-surface inspection result."""

    root_attempt = _import_module("vllm")
    imports: dict[str, Any] = {"vllm": root_attempt.to_dict()}
    warnings: list[str] = []
    unsupported_reasons: list[str] = []
    version = root_attempt.version or _metadata_version(VLLM_DISTRIBUTIONS)

    if not root_attempt.imported:
        reason = "vLLM is not importable in this environment."
        unsupported_reasons.append(reason)
        status = (
            STATUS_NOT_INSTALLED
            if root_attempt.error_type == "ModuleNotFoundError"
            else STATUS_ERROR
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "vllm_version": version,
            "python": _python_details(),
            "imports": imports,
            "kv_transfer_config": _missing_kv_transfer_config(reason),
            "config_fields": _empty_expected_field_map(),
            "connector_base": _missing_connector_base(reason),
            "connector_base_methods": _empty_expected_method_map(),
            "dynamic_connector": _missing_dynamic_support(reason),
            "dynamic_connector_supported": False,
            "available_kv_connectors": _missing_available_connectors(reason),
            "warnings": warnings,
            "unsupported_reasons": unsupported_reasons,
        }

    config = inspect_kv_transfer_config()
    connector_base = inspect_kv_connector_base_v1()
    dynamic = inspect_dynamic_connector_support()
    available_connectors = inspect_available_kv_connector_modules()
    _merge_imports(
        imports,
        config.get("imports", {}),
        connector_base.get("imports", {}),
        dynamic.get("imports", {}),
        available_connectors.get("imports", {}),
    )

    for section in (config, connector_base, dynamic, available_connectors):
        warnings.extend(str(item) for item in section.get("warnings", []))
        unsupported_reasons.extend(
            str(item) for item in section.get("unsupported_reasons", [])
        )

    config_available = bool(config.get("available"))
    base_available = bool(connector_base.get("available"))
    dynamic_supported = bool(dynamic.get("supported"))
    missing_expected = list(config.get("missing_expected_fields", []))
    required_surface_available = config_available and base_available and dynamic_supported
    status = STATUS_INSTALLED if required_surface_available else STATUS_PARTIAL
    if missing_expected:
        warnings.append(
            "KVTransferConfig is missing expected fields: "
            + ", ".join(missing_expected)
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "vllm_version": version,
        "python": _python_details(),
        "imports": imports,
        "kv_transfer_config": config,
        "config_fields": config.get("fields", _empty_expected_field_map()),
        "connector_base": connector_base,
        "connector_base_methods": connector_base.get(
            "methods",
            _empty_expected_method_map(),
        ),
        "dynamic_connector": dynamic,
        "dynamic_connector_supported": dynamic_supported,
        "available_kv_connectors": available_connectors,
        "warnings": _dedupe(warnings),
        "unsupported_reasons": _dedupe(unsupported_reasons),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect the installed vLLM KVTransfer API surface for BIFROST Phase 7"
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--output", default=None, help="Optional path to write JSON output")
    parser.add_argument("--verbose", action="store_true", help="Include detailed text output")
    try:
        args = parser.parse_args(argv)
        result = inspect_result()
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(_to_json(result, indent=2) + "\n", encoding="utf-8")
        if args.json:
            print(_to_json(result, indent=2))
        else:
            print(format_text_report(result, verbose=args.verbose))
        return 0
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"bifrost vLLM API inspector failed: {exc}", file=sys.stderr)
        return 2


def format_text_report(result: dict[str, Any], *, verbose: bool = False) -> str:
    lines = [
        "BIFROST Phase 7 vLLM API inspector",
        f"status: {result.get('status')}",
        f"vllm_version: {result.get('vllm_version')}",
        f"dynamic_connector_supported: {result.get('dynamic_connector_supported')}",
    ]
    if verbose:
        lines.append("")
        lines.append("Imports:")
        for name, details in sorted(result.get("imports", {}).items()):
            marker = "ok" if details.get("imported") else "missing"
            lines.append(f"- {name}: {marker}")
            if details.get("error"):
                lines.append(f"  error: {details['error']}")
        lines.append("")
        lines.append("Config fields:")
        for name, details in result.get("config_fields", {}).items():
            lines.append(f"- {name}: {'present' if details.get('present') else 'missing'}")
        lines.append("")
        lines.append("Connector base methods:")
        for name, details in result.get("connector_base_methods", {}).items():
            if details.get("present"):
                lines.append(f"- {name}{details.get('signature') or ''}")
            else:
                lines.append(f"- {name}: missing")
    if result.get("warnings"):
        lines.append("")
        lines.append("Warnings:")
        for warning in result["warnings"]:
            lines.append(f"- {warning}")
    if result.get("unsupported_reasons"):
        lines.append("")
        lines.append("Unsupported reasons:")
        for reason in result["unsupported_reasons"]:
            lines.append(f"- {reason}")
    return "\n".join(lines)


def _import_module(name: str) -> ImportAttempt:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            module = importlib.import_module(name)
    except Exception as exc:
        return ImportAttempt(
            name=name,
            imported=False,
            error=repr(exc),
            error_type=exc.__class__.__name__,
            import_stdout=stdout.getvalue(),
            import_stderr=stderr.getvalue(),
        )
    return ImportAttempt(
        name=name,
        imported=True,
        module=module,
        module_path=str(getattr(module, "__file__", "") or ""),
        version=_module_version(module, VLLM_DISTRIBUTIONS if name == "vllm" else ()),
        import_stdout=stdout.getvalue(),
        import_stderr=stderr.getvalue(),
    )


def _module_version(module: ModuleType, distributions: tuple[str, ...]) -> str | None:
    version = getattr(module, "__version__", None)
    if version:
        return str(version)
    return _metadata_version(distributions)


def _metadata_version(distributions: tuple[str, ...]) -> str | None:
    for distribution in distributions:
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
        except Exception:
            continue
    return None


def _python_details() -> dict[str, Any]:
    return {
        "executable": sys.executable,
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
    }


def _signature_details(callable_obj: Any) -> dict[str, Any]:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError) as exc:
        return {
            "signature": None,
            "parameters": [],
            "return_annotation": None,
            "error": repr(exc),
        }
    return {
        "signature": _stable_signature(signature),
        "parameters": [_parameter_details(param) for param in signature.parameters.values()],
        "return_annotation": _annotation_value(signature.return_annotation),
    }


def _stable_signature(signature: inspect.Signature) -> str:
    return _sanitize_repr(str(signature))


def _parameter_details(param: inspect.Parameter) -> dict[str, Any]:
    return {
        "name": param.name,
        "kind": param.kind.name,
        "annotation": _annotation_value(param.annotation),
        "default": _default_value(param.default),
        "required": param.default is inspect.Signature.empty
        and param.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD),
    }


def _annotation_details(cls: Any) -> dict[str, str]:
    annotations = getattr(cls, "__annotations__", {}) or {}
    return {
        str(name): _annotation_value(annotation)
        for name, annotation in sorted(annotations.items(), key=lambda item: str(item[0]))
    }


def _dataclass_field_details(cls: Any) -> dict[str, Any]:
    if not dataclasses.is_dataclass(cls):
        return {}
    details: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        item: dict[str, Any] = {
            "type": _annotation_value(field.type),
            "init": field.init,
            "repr": field.repr,
            "compare": field.compare,
            "default": _default_value(field.default),
            "default_factory": (
                None
                if field.default_factory is dataclasses.MISSING
                else _stable_object_name(field.default_factory)
            ),
        }
        details[field.name] = item
    return details


def _collect_config_fields(
    signature: dict[str, Any],
    annotations: dict[str, str],
    dataclass_fields: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    field_sources: dict[str, set[str]] = {}
    parameter_details: dict[str, dict[str, Any]] = {}
    for param in signature.get("parameters", []):
        name = str(param["name"])
        if name in {"self", "cls"}:
            continue
        if param["kind"] in {"VAR_POSITIONAL", "VAR_KEYWORD"}:
            continue
        field_sources.setdefault(name, set()).add("signature")
        parameter_details[name] = param
    for name in annotations:
        field_sources.setdefault(name, set()).add("annotations")
    for name in dataclass_fields:
        field_sources.setdefault(name, set()).add("dataclass_fields")

    ordered_names = sorted(set(EXPECTED_CONFIG_FIELDS).union(field_sources))
    fields: dict[str, dict[str, Any]] = {}
    for name in ordered_names:
        fields[name] = {
            "present": name in field_sources,
            "sources": sorted(field_sources.get(name, set())),
        }
        if name in parameter_details:
            fields[name]["signature_parameter"] = parameter_details[name]
        if name in annotations:
            fields[name]["annotation"] = annotations[name]
        if name in dataclass_fields:
            fields[name]["dataclass_field"] = dataclass_fields[name]
    return fields


def _connector_method_details(base_cls: Any) -> dict[str, dict[str, Any]]:
    present_methods: dict[str, dict[str, Any]] = {}
    for name, member in inspect.getmembers(base_cls):
        if name != "__init__" and name.startswith("_"):
            continue
        raw_member = inspect.getattr_static(base_cls, name, member)
        callable_obj = _unwrap_callable(raw_member, member)
        if callable_obj is None:
            continue
        signature = _signature_details(callable_obj)
        present_methods[name] = {
            "present": True,
            "signature": signature["signature"],
            "signature_parameters": signature["parameters"],
            "return_annotation": signature["return_annotation"],
            "coroutine": inspect.iscoroutinefunction(callable_obj),
            "abstract": bool(getattr(callable_obj, "__isabstractmethod__", False)),
            "qualname": str(getattr(callable_obj, "__qualname__", "")),
        }
        if "error" in signature:
            present_methods[name]["signature_error"] = signature["error"]

    ordered_names = list(EXPECTED_CONNECTOR_METHODS)
    extras = sorted(name for name in present_methods if name not in EXPECTED_CONNECTOR_METHODS)
    methods: dict[str, dict[str, Any]] = {}
    for name in ordered_names + extras:
        methods[name] = present_methods.get(
            name,
            {
                "present": False,
                "signature": None,
                "signature_parameters": [],
                "return_annotation": None,
                "coroutine": False,
                "abstract": False,
            },
        )
    return methods


def _unwrap_callable(raw_member: Any, bound_member: Any) -> Any | None:
    if isinstance(raw_member, (staticmethod, classmethod)):
        candidate = raw_member.__func__
    elif isinstance(raw_member, property):
        return None
    else:
        candidate = raw_member
    if callable(candidate):
        return candidate
    if callable(bound_member):
        return bound_member
    return None


def _discover_connector_classes(
    module: ModuleType,
    base_cls: Any | None,
) -> list[dict[str, Any]]:
    classes: list[dict[str, Any]] = []
    for name, cls in inspect.getmembers(module, inspect.isclass):
        if name.startswith("_") or name == KV_CONNECTOR_BASE_NAME:
            continue
        if not _looks_like_connector_class(name, cls, base_cls):
            continue
        signature = _signature_details(cls)
        classes.append(
            {
                "name": name,
                "module": str(getattr(cls, "__module__", "")),
                "qualname": str(getattr(cls, "__qualname__", "")),
                "signature": signature["signature"],
                "signature_parameters": signature["parameters"],
                "subclass_of_base_v1": _is_subclass(cls, base_cls),
            }
        )
    return classes


def _looks_like_connector_class(name: str, cls: Any, base_cls: Any | None) -> bool:
    if _is_subclass(cls, base_cls):
        return True
    return "Connector" in name or "KVConnector" in name


def _is_subclass(cls: Any, base_cls: Any | None) -> bool:
    if base_cls is None or cls is base_cls:
        return False
    try:
        return issubclass(cls, base_cls)
    except TypeError:
        return False


def _inspect_vllm_cli_flags(*, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {
            "checked": False,
            "available": False,
            "path": None,
            "has_kv_transfer_config_flag": False,
            "flags": [],
            "reason": "KVTransfer package is not importable; CLI flag check skipped.",
        }
    if os.environ.get("BIFROST_VLLM_INSPECT_SKIP_CLI") == "1":
        return {
            "checked": False,
            "available": False,
            "path": None,
            "has_kv_transfer_config_flag": False,
            "flags": [],
            "reason": "BIFROST_VLLM_INSPECT_SKIP_CLI=1",
        }
    path = shutil.which("vllm")
    if not path:
        return {
            "checked": False,
            "available": False,
            "path": None,
            "has_kv_transfer_config_flag": False,
            "flags": [],
            "reason": "vLLM CLI is not on PATH.",
        }
    try:
        completed = subprocess.run(
            [path, "serve", "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
    except Exception as exc:
        return {
            "checked": True,
            "available": False,
            "path": path,
            "has_kv_transfer_config_flag": False,
            "flags": [],
            "error": repr(exc),
        }
    output = f"{completed.stdout}\n{completed.stderr}"
    flags = sorted(_OPTION_RE.findall(output))
    return {
        "checked": True,
        "available": completed.returncode == 0,
        "path": path,
        "returncode": completed.returncode,
        "has_kv_transfer_config_flag": "--kv-transfer-config" in flags
        or "--kv-transfer-config" in output,
        "flags": [
            flag
            for flag in flags
            if "kv" in flag.lower() or "transfer" in flag.lower()
        ],
        "stderr_tail": completed.stderr[-1000:],
    }


def _annotation_value(value: Any) -> str | None:
    if value is inspect.Signature.empty:
        return None
    if isinstance(value, str):
        return value
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if module and qualname:
        if module == "builtins":
            return str(qualname)
        return f"{module}.{qualname}"
    return _sanitize_repr(str(value))


def _default_value(value: Any) -> str | None:
    if value is inspect.Signature.empty or value is dataclasses.MISSING:
        return None
    return _sanitize_repr(repr(value))


def _stable_object_name(value: Any) -> str:
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if module and qualname:
        return f"{module}.{qualname}" if module != "builtins" else str(qualname)
    return _sanitize_repr(repr(value))


def _sanitize_repr(text: str) -> str:
    return _ADDRESS_RE.sub(" at 0x...", text)


def _empty_expected_field_map() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "present": False,
            "sources": [],
        }
        for name in EXPECTED_CONFIG_FIELDS
    }


def _empty_expected_method_map() -> dict[str, dict[str, Any]]:
    return {
        name: {
            "present": False,
            "signature": None,
            "signature_parameters": [],
            "return_annotation": None,
            "coroutine": False,
            "abstract": False,
        }
        for name in EXPECTED_CONNECTOR_METHODS
    }


def _missing_kv_transfer_config(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "module": VLLM_CONFIG_MODULE,
        "class_name": "KVTransferConfig",
        "imports": {},
        "signature": None,
        "signature_parameters": [],
        "annotations": {},
        "dataclass_fields": {},
        "fields": _empty_expected_field_map(),
        "expected_fields": {name: False for name in EXPECTED_CONFIG_FIELDS},
        "missing_expected_fields": list(EXPECTED_CONFIG_FIELDS),
        "warnings": [],
        "unsupported_reasons": [reason],
    }


def _missing_connector_base(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "module": KV_CONNECTOR_BASE_MODULE,
        "class_name": KV_CONNECTOR_BASE_NAME,
        "imports": {},
        "methods": _empty_expected_method_map(),
        "public_method_names": [],
        "abstract_methods": [],
        "warnings": [],
        "unsupported_reasons": [reason],
    }


def _missing_dynamic_support(reason: str) -> dict[str, Any]:
    return {
        "supported": False,
        "imports": {},
        "signals": {
            "kv_transfer_module_importable": False,
            "kv_connector_field": False,
            "kv_connector_module_path_field": False,
            "kv_connector_extra_config_field": False,
            "serve_cli_has_kv_transfer_config_flag": False,
        },
        "cli": {
            "checked": False,
            "available": False,
            "path": None,
            "has_kv_transfer_config_flag": False,
            "flags": [],
            "reason": reason,
        },
        "warnings": [],
        "unsupported_reasons": [reason],
    }


def _missing_available_connectors(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "package": KV_CONNECTOR_V1_PACKAGE,
        "imports": {},
        "modules": [],
        "connector_classes": [],
        "warnings": [],
        "unsupported_reasons": [reason],
    }


def _merge_imports(target: dict[str, Any], *sources: dict[str, Any]) -> None:
    for source in sources:
        for name, details in source.items():
            target[name] = details


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _to_json(data: dict[str, Any], *, indent: int | None) -> str:
    return json.dumps(data, indent=indent, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
