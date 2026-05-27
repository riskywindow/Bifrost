"""Canonical JSON encoding for BIFROST Phase 1 descriptors."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def canonical_encode(obj: Mapping[str, Any]) -> bytes:
    """Return canonical JSON UTF-8 bytes for a JSON object.

    Phase 1 deliberately accepts only deterministic JSON values. Floats are
    rejected before encoding so NaN, infinity, and implementation-specific
    number spellings cannot enter object identity.
    """

    if not isinstance(obj, Mapping):
        raise TypeError("$: canonical descriptor must be an object")

    _validate_canonical_value(obj, path="$")
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _validate_canonical_value(value: Any, *, path: str) -> None:
    if value is None or isinstance(value, str) or isinstance(value, bool):
        return

    if isinstance(value, float):
        raise TypeError(f"{path}: floats are not supported in canonical JSON")

    if isinstance(value, int):
        return

    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path}: object keys must be strings")
            _validate_canonical_value(item, path=f"{path}.{key}")
        return

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _validate_canonical_value(item, path=f"{path}[{index}]")
        return

    raise TypeError(
        f"{path}: unsupported canonical JSON value {type(value).__name__}"
    )
