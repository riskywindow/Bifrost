"""Stable LMCache CacheEngineKey representation and hashing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
import inspect
import re
from typing import Any

from blake3 import blake3

from bifrost_kv.canonical import canonical_encode
from lmcache_bifrost.errors import KeyCodecError

KEY_HASH_DOMAIN = b"bifrost.lmcache.key.v1\x00"
HASH_PREFIX = "blake3:"
_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{6,}")


def stable_key_repr(key: object) -> str:
    """Return a deterministic representation for an LMCache CacheEngineKey."""

    try:
        value = _stable_key_value(key)
        return canonical_encode({"lmcache_cache_engine_key": value}).decode("utf-8")
    except KeyCodecError:
        raise
    except Exception as exc:  # pragma: no cover - defensive fail-closed wrapper.
        raise KeyCodecError(f"failed to canonicalize LMCache key: {exc}") from exc


def opaque_engine_key_hash(key: object) -> str:
    """Return the BIFROST opaque engine key hash for an LMCache key."""

    canonical_repr = stable_key_repr(key).encode("utf-8")
    digest = blake3(KEY_HASH_DOMAIN + canonical_repr).hexdigest()
    return f"{HASH_PREFIX}{digest}"


def _stable_key_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        raise KeyCodecError("float key fields are not supported")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__bytes_hex__": bytes(value).hex()}
    if isinstance(value, Mapping):
        return {
            str(key): _stable_key_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, tuple) and hasattr(value, "_fields"):
        return {
            "__module__": value.__class__.__module__,
            "__type__": value.__class__.__qualname__,
            "fields": {
                name: _stable_key_value(getattr(value, name))
                for name in sorted(value._fields)
            },
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "__module__": value.__class__.__module__,
            "__type__": value.__class__.__qualname__,
            "fields": {
                field.name: _stable_key_value(getattr(value, field.name))
                for field in sorted(fields(value), key=lambda item: item.name)
                if not field.name.startswith("_")
            },
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_stable_key_value(item) for item in value]

    hook_value = _stable_hook_value(value)
    if hook_value is not _NoHook:
        return hook_value

    public_fields = _public_field_values(value)
    if public_fields:
        return {
            "__module__": value.__class__.__module__,
            "__type__": value.__class__.__qualname__,
            "fields": public_fields,
        }

    raise KeyCodecError(
        f"{value.__class__.__module__}.{value.__class__.__qualname__} does not expose "
        "stable key serialization or public fields"
    )


class _NoHookType:
    pass


_NoHook = _NoHookType()


def _stable_hook_value(value: Any) -> Any:
    for name in (
        "to_canonical_string",
        "canonical_string",
        "stable_repr",
        "to_stable_repr",
        "to_string",
        "serialize",
        "to_bytes",
    ):
        attr = getattr(value, name, None)
        if attr is None:
            continue
        candidate = attr() if callable(attr) else attr
        if candidate is None:
            continue
        return {
            "__module__": value.__class__.__module__,
            "__type__": value.__class__.__qualname__,
            "__stable_hook__": name,
            "value": _stable_serialized_value(candidate),
        }

    text = _safe_custom_text(value)
    if text is not None:
        return {
            "__module__": value.__class__.__module__,
            "__type__": value.__class__.__qualname__,
            "__stable_hook__": "__str__",
            "value": text,
        }
    return _NoHook


def _stable_serialized_value(candidate: Any) -> Any:
    if isinstance(candidate, (bytes, bytearray, memoryview)):
        return {"__bytes_hex__": bytes(candidate).hex()}
    return _stable_key_value(candidate)


def _safe_custom_text(value: Any) -> str | None:
    custom_repr = value.__class__.__repr__ is not object.__repr__
    custom_str = value.__class__.__str__ is not object.__str__
    if not custom_repr and not custom_str:
        return None
    text = str(value)
    if _ADDRESS_RE.search(text):
        raise KeyCodecError("key string representation contains a memory address")
    if "\n" in text or "\r" in text:
        raise KeyCodecError("key string representation must be single-line")
    return text


def _public_field_values(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    annotations = getattr(value.__class__, "__annotations__", {})
    for name in sorted(annotations):
        if not name.startswith("_") and hasattr(value, name):
            result[name] = _stable_key_value(getattr(value, name))

    if hasattr(value, "__dict__"):
        for name, item in sorted(vars(value).items()):
            if not name.startswith("_") and not inspect.ismethod(item):
                result.setdefault(name, _stable_key_value(item))
    return result


__all__ = ["KEY_HASH_DOMAIN", "opaque_engine_key_hash", "stable_key_repr"]
