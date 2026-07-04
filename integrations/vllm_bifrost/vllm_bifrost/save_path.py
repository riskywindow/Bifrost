"""Save-path helpers for BIFROST vLLM opaque KV blobs.

This module is intentionally imported lazily by ``connector.py`` so that a
plain dynamic import of the connector does not pull in torch or the BIFROST
client packages.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .config import BifrostVLLMConnectorConfig
from .errors import (
    CPUStagingSerializationError,
    DaemonUnavailableError,
    KeyHashingError,
    OpaqueBlobValidationError,
    SchedulerMetadataError,
    StoreCommitError,
    UnsupportedOperationError,
)
from .keying import (
    stable_kv_cache_config_hash,
    stable_layout_fingerprint,
)


@dataclass(frozen=True, slots=True)
class SaveResult:
    request_id: str
    layer_name: str
    block_ids: tuple[int, ...]
    object_id: str
    blob_key_hash: str
    bytes_saved: int
    metadata: dict[str, Any]

    def to_record(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "layer_name": self.layer_name,
            "block_ids": list(self.block_ids),
            "object_id": self.object_id,
            "blob_key_hash": self.blob_key_hash,
            "bytes_saved": self.bytes_saved,
        }


@dataclass(frozen=True, slots=True)
class _StagedPayload:
    payload: bytes
    tensor_shape: tuple[int, ...]
    tensor_dtype: object
    device_origin: str


class VLLMSavePath:
    """Synchronous fake-vLLM save path backed by ``BifrostClient.put_object``."""

    def __init__(
        self,
        *,
        config: BifrostVLLMConnectorConfig,
        connector_instance_id: str,
        vllm_config: object | None = None,
        role: object | None = None,
        client: object | None = None,
    ) -> None:
        self.config = config
        self.connector_instance_id = connector_instance_id
        self.vllm_config = vllm_config
        self.role = role
        self.client = client
        self._owns_client = client is None
        self._kv_caches: object | None = None
        self._connector_metadata: list[object] = []
        self._save_errors: list[Exception] = []

    def register_kv_caches(self, kv_caches: object | None) -> None:
        self._kv_caches = kv_caches

    def remember_connector_metadata(self, value: object | None) -> None:
        for item in _metadata_entries(value):
            self._connector_metadata.append(item)

    def clear_request(self, request_id: str | None) -> None:
        if request_id is None:
            return
        self._connector_metadata = [
            item
            for item in self._connector_metadata
            if _field(item, ("request_id", "req_id")) != request_id
        ]

    def save_kv_layer(
        self,
        *,
        layer_name: str | None,
        kv_layer: object | None,
        attn_metadata: object | None,
        kwargs: Mapping[str, Any],
    ) -> SaveResult:
        if self.config.save_mode == "async":
            raise UnsupportedOperationError(
                "async save_mode is not implemented for the Phase 7 save path",
                reason_code="async_save_unsupported",
            )

        save_identity = self._derive_save_identity(layer_name, attn_metadata, kwargs)
        resolved_layer = _resolve_layer(
            explicit_layer=kv_layer,
            kv_caches=self._kv_caches,
            layer_name=save_identity.layer_name,
        )
        staged = _stage_payload(
            resolved_layer,
            save_identity.block_ids,
            allow_cpu_staging=self.config.allow_cpu_staging,
        )
        model_fingerprint = _model_fingerprint(
            self.config,
            self.vllm_config,
        )
        kv_cache_config_hash = _kv_cache_config_hash(
            self.vllm_config,
            self._kv_caches,
        )
        layout_fingerprint = _layout_fingerprint(
            self.config,
            self.vllm_config,
            kv_cache_config_hash,
            model_fingerprint,
            staged,
        )
        role = _role_name(self.role, self.vllm_config)
        vllm_version = _optional_text(
            _field(self.vllm_config, ("vllm_version", "__version__"))
        )
        connector_api_version = _optional_text(
            _field(self.vllm_config, ("connector_api_version", "kvtransfer_api_version"))
        )

        try:
            from .blob_codec import (
                build_vllm_opaque_metadata,
                build_vllm_opaque_target_profile,
            )
            from bifrost_kv.validate import validate_object
        except Exception as exc:  # pragma: no cover - environment setup failure.
            raise OpaqueBlobValidationError(
                f"failed to import vLLM opaque blob validators: {exc}"
            ) from exc

        try:
            metadata = build_vllm_opaque_metadata(
                payload=staged.payload,
                connector_instance_id=self.connector_instance_id,
                request_id=save_identity.request_id,
                model_fingerprint=model_fingerprint,
                kv_cache_config_hash=kv_cache_config_hash,
                layer_name=save_identity.layer_name,
                block_ids=save_identity.block_ids,
                role=role,
                vllm_version=vllm_version,
                connector_api_version=connector_api_version,
                layout_fingerprint=layout_fingerprint,
                tensor_shape=staged.tensor_shape,
                tensor_dtype=staged.tensor_dtype,
                device_origin=staged.device_origin,
                config=self.config,
            )
            target = build_vllm_opaque_target_profile(
                connector_instance_id=self.connector_instance_id,
                request_id=save_identity.request_id,
                model_fingerprint=model_fingerprint,
                kv_cache_config_hash=kv_cache_config_hash,
                layer_name=save_identity.layer_name,
                block_ids=save_identity.block_ids,
                role=role,
                vllm_version=vllm_version,
                layout_fingerprint=layout_fingerprint,
                config=self.config,
            )
            validation = validate_object(metadata, staged.payload, target)
            if validation.status != "accepted":
                raise OpaqueBlobValidationError(
                    "generated vLLM opaque blob failed validation: "
                    f"{validation.reason_code}"
                )
            if validation.object_id != metadata.get("object_id"):
                raise OpaqueBlobValidationError(
                    "generated vLLM opaque blob object_id mismatch"
                )
        except (OpaqueBlobValidationError, KeyHashingError):
            raise
        except Exception as exc:
            raise OpaqueBlobValidationError(
                f"failed to build opaque vLLM object: {exc}"
            ) from exc

        client = self._client()
        object_id = str(metadata["object_id"])
        try:
            result = client.put_object(metadata, staged.payload, self.config.chunk_size)
        except Exception as exc:
            raise _wrap_put_error(exc) from exc

        if not bool(getattr(result, "stored", False)) or not bool(
            getattr(result, "verified", False)
        ):
            reason = getattr(result, "reason", "put_not_verified")
            raise StoreCommitError(f"BIFROST PUT was not verified: {reason}")
        if getattr(result, "object_id", object_id) != object_id:
            raise StoreCommitError("BIFROST PUT returned the wrong object_id")

        opaque = metadata.get("opaque_engine_profile")
        if not isinstance(opaque, dict):
            raise OpaqueBlobValidationError("generated object missing opaque profile")
        key_hash = opaque.get("engine_key_hash")
        if not isinstance(key_hash, str) or not key_hash:
            raise OpaqueBlobValidationError("generated object missing key hash")
        return SaveResult(
            request_id=save_identity.request_id,
            layer_name=save_identity.layer_name,
            block_ids=save_identity.block_ids,
            object_id=object_id,
            blob_key_hash=key_hash,
            bytes_saved=len(staged.payload),
            metadata=metadata,
        )

    def record_error(self, exc: Exception) -> None:
        self._save_errors.append(exc)

    def pop_save_error(self) -> Exception | None:
        if not self._save_errors:
            return None
        return self._save_errors.pop(0)

    def close(self) -> None:
        if not self._owns_client:
            return
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
        self.client = None

    def _derive_save_identity(
        self,
        layer_name: str | None,
        attn_metadata: object | None,
        kwargs: Mapping[str, Any],
    ) -> "_SaveIdentity":
        metadata_candidates: list[object] = []
        if "connector_metadata" in kwargs:
            metadata_candidates.extend(_metadata_entries(kwargs["connector_metadata"]))
        if "metadata" in kwargs:
            metadata_candidates.extend(_metadata_entries(kwargs["metadata"]))
        metadata_candidates.extend(
            _metadata_entries(
                _connector_metadata_from_attention(
                    attn_metadata,
                    connector_instance_id=self.connector_instance_id,
                )
            )
        )
        metadata_candidates.extend(_metadata_entries(attn_metadata))
        metadata_candidates.extend(self._connector_metadata)

        selected = _select_metadata(metadata_candidates, layer_name)
        resolved_layer_name = _first_text(
            kwargs.get("layer_name"),
            layer_name,
            _first_layer_name(selected),
            _first_layer_name(attn_metadata),
        )
        if resolved_layer_name is None:
            raise SchedulerMetadataError("save_kv_layer requires a stable layer_name")

        request_id = _first_text(
            kwargs.get("request_id"),
            _field(selected, ("request_id", "req_id")),
            _field(attn_metadata, ("request_id", "req_id")),
            _prefix_request_id(selected),
            _prefix_request_id(attn_metadata),
        )
        if request_id is None:
            raise SchedulerMetadataError("save_kv_layer requires a stable request_id")

        block_ids = _first_block_ids(
            kwargs.get("block_ids"),
            kwargs.get("block_id"),
            kwargs.get("blocks"),
            _field(selected, ("block_ids", "block_id", "blocks")),
            _field(attn_metadata, ("block_ids", "block_id", "blocks")),
        )
        if not block_ids:
            raise SchedulerMetadataError("save_kv_layer requires non-empty block_ids")

        return _SaveIdentity(
            request_id=request_id,
            layer_name=resolved_layer_name,
            block_ids=block_ids,
        )

    def _client(self) -> object:
        if self.client is None:
            try:
                from bifrost_client import BifrostClient, BifrostClientConfig
            except Exception as exc:  # pragma: no cover - environment setup failure.
                raise DaemonUnavailableError(
                    f"BIFROST Python client is unavailable: {exc}"
                ) from exc
            self.client = BifrostClient(
                config=BifrostClientConfig(
                    endpoint=self.config.endpoint,
                    timeout_seconds=self.config.timeout_seconds,
                    default_chunk_size=self.config.chunk_size,
                )
            )
        return self.client


@dataclass(frozen=True, slots=True)
class _SaveIdentity:
    request_id: str
    layer_name: str
    block_ids: tuple[int, ...]


def _connector_metadata_from_attention(
    attn_metadata: object | None,
    *,
    connector_instance_id: str,
) -> object | None:
    if attn_metadata is None:
        return None
    build = getattr(attn_metadata, "to_connector_metadata", None)
    if not callable(build):
        return None
    try:
        return build(operation="save", connector_instance_id=connector_instance_id)
    except TypeError:
        return build("save", connector_instance_id)


def _select_metadata(candidates: Sequence[object], layer_name: str | None) -> object | None:
    if layer_name is None:
        return candidates[0] if candidates else None
    for candidate in candidates:
        layer_names = _layer_names(candidate)
        if not layer_names or layer_name in layer_names:
            return candidate
    return candidates[0] if candidates else None


def _metadata_entries(value: object | None) -> list[object]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, (str, bytes, bytearray, memoryview)):
        return [value]
    if isinstance(value, Iterable):
        return list(value)
    return [value]


def _resolve_layer(
    *,
    explicit_layer: object | None,
    kv_caches: object | None,
    layer_name: str,
) -> object:
    if explicit_layer is not None:
        return explicit_layer
    if isinstance(kv_caches, Mapping) and layer_name in kv_caches:
        return kv_caches[layer_name]
    get = getattr(kv_caches, "get", None)
    if callable(get):
        candidate = get(layer_name)
        if candidate is not None:
            return candidate
    raise CPUStagingSerializationError(
        f"save_kv_layer could not resolve KV layer {layer_name!r}"
    )


def _stage_payload(
    kv_layer: object,
    block_ids: tuple[int, ...],
    *,
    allow_cpu_staging: bool,
) -> _StagedPayload:
    if isinstance(kv_layer, (bytes, bytearray, memoryview)):
        payload = bytes(kv_layer)
        if not payload:
            raise CPUStagingSerializationError("KV payload must be non-empty")
        try:
            import torch
        except Exception as exc:  # pragma: no cover - test env always has torch.
            raise CPUStagingSerializationError(
                f"byte payload staging requires torch dtype metadata: {exc}"
            ) from exc
        return _StagedPayload(
            payload=payload,
            tensor_shape=(len(payload),),
            tensor_dtype=torch.uint8,
            device_origin="bytes",
        )

    try:
        import torch
        from .blob_codec import tensor_to_payload
    except Exception as exc:  # pragma: no cover - environment setup failure.
        raise CPUStagingSerializationError(
            f"CPU staging imports failed: {exc}"
        ) from exc

    if not torch.is_tensor(kv_layer):
        raise CPUStagingSerializationError(
            "save_kv_layer requires a torch.Tensor or byte payload"
        )
    payload_tensor = _select_tensor_blocks(kv_layer, block_ids)
    payload = tensor_to_payload(payload_tensor, allow_cpu_staging=allow_cpu_staging)
    if not payload:
        raise CPUStagingSerializationError("KV payload must be non-empty")
    return _StagedPayload(
        payload=payload,
        tensor_shape=tuple(int(dim) for dim in payload_tensor.shape),
        tensor_dtype=payload_tensor.dtype,
        device_origin=str(kv_layer.device),
    )


def _select_tensor_blocks(kv_layer: object, block_ids: tuple[int, ...]) -> object:
    try:
        import torch
    except Exception as exc:  # pragma: no cover - environment setup failure.
        raise CPUStagingSerializationError(
            f"CPU staging imports failed: {exc}"
        ) from exc

    if not torch.is_tensor(kv_layer):
        raise CPUStagingSerializationError(
            "save_kv_layer requires a torch.Tensor or byte payload"
        )

    if kv_layer.dim() == 5:
        try:
            from .fakes import flatten_layer_blocks

            return flatten_layer_blocks(kv_layer, block_ids)
        except Exception as exc:
            raise CPUStagingSerializationError(
                f"failed to stage selected KV blocks: {exc}"
            ) from exc

    try:
        if kv_layer.dim() == 0:
            raise CPUStagingSerializationError("KV tensor must have a block dimension")
        num_blocks = int(kv_layer.shape[0])
        ids = tuple(
            _normalize_block_id(block_id, num_blocks=num_blocks)
            for block_id in block_ids
        )
        index = torch.tensor(ids, dtype=torch.long, device="cpu")
        return kv_layer.index_select(0, index).detach().clone().contiguous()
    except CPUStagingSerializationError:
        raise
    except Exception as exc:
        raise CPUStagingSerializationError(
            f"failed to stage selected KV blocks: {exc}"
        ) from exc


def _model_fingerprint(
    config: BifrostVLLMConnectorConfig,
    vllm_config: object | None,
) -> str:
    if config.model_fingerprint is not None:
        return config.model_fingerprint
    model_config = _field(vllm_config, ("model_config", "model"))
    if model_config is None:
        return "model:unknown"
    return stable_kv_cache_config_hash(model_config)


def _kv_cache_config_hash(vllm_config: object | None, kv_caches: object | None) -> str:
    kv_cache_config = _field(vllm_config, ("kv_cache_config", "cache_config"))
    if kv_cache_config is not None:
        return stable_kv_cache_config_hash(kv_cache_config)
    if isinstance(kv_caches, Mapping):
        material = {
            "layer_names": sorted(str(key) for key in kv_caches),
            "layer_shapes": {
                str(key): list(getattr(value, "shape", ()))
                for key, value in sorted(kv_caches.items(), key=lambda item: str(item[0]))
            },
            "layer_dtypes": {
                str(key): str(getattr(value, "dtype", "unknown"))
                for key, value in sorted(kv_caches.items(), key=lambda item: str(item[0]))
            },
        }
        return stable_kv_cache_config_hash(material)
    return stable_kv_cache_config_hash({"kv_cache_config": "unknown"})


def _layout_fingerprint(
    config: BifrostVLLMConnectorConfig,
    vllm_config: object | None,
    kv_cache_config_hash: str,
    model_fingerprint: str,
    staged: _StagedPayload,
) -> str:
    if config.layout_fingerprint is not None:
        return config.layout_fingerprint
    kv_cache_config = _field(vllm_config, ("kv_cache_config", "cache_config"))
    explicit = _field(kv_cache_config, ("layout_fingerprint",))
    extra: dict[str, Any] = {}
    if explicit is not None:
        extra["provided_layout_fingerprint"] = str(explicit)
    return stable_layout_fingerprint(
        kv_cache_config_hash=kv_cache_config_hash,
        model_fingerprint=model_fingerprint,
        vllm_version=_optional_text(
            _field(vllm_config, ("vllm_version", "__version__"))
        ),
        connector_api_version=_optional_text(
            _field(vllm_config, ("connector_api_version", "kvtransfer_api_version"))
        ),
        tensor_dtype=staged.tensor_dtype,
        tensor_shape=staged.tensor_shape,
        extra=extra or None,
    )


def _role_name(role: object | None, vllm_config: object | None) -> str:
    candidate = role
    if candidate is None:
        transfer_config = _field(vllm_config, ("kv_transfer_config",))
        candidate = _field(transfer_config, ("role", "kv_role"))
    value = getattr(candidate, "value", candidate)
    if value is None:
        return "kv_both"
    return str(value)


def _first_text(*values: object) -> str | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
        else:
            text = str(value).strip()
        if text:
            return text
    return None


def _first_layer_name(value: object | None) -> str | None:
    names = _layer_names(value)
    return names[0] if names else None


def _layer_names(value: object | None) -> tuple[str, ...]:
    names = _field(value, ("layer_names", "layers"))
    if names is None:
        name = _field(value, ("layer_name",))
        return (str(name),) if name is not None else ()
    if isinstance(names, str):
        return (names,)
    try:
        return tuple(str(name) for name in names)
    except TypeError:
        return (str(names),)


def _prefix_request_id(value: object | None) -> str | None:
    prefix_hash = _field(value, ("prefix_hash", "prefix_id"))
    if prefix_hash is None:
        return None
    return f"prefix:{prefix_hash}"


def _first_block_ids(*values: object) -> tuple[int, ...]:
    for value in values:
        if value is None:
            continue
        ids = _coerce_block_ids(value)
        if ids:
            return ids
    return ()


def _coerce_block_ids(value: object) -> tuple[int, ...]:
    if isinstance(value, bool):
        raise SchedulerMetadataError("block_ids entries must be integers")
    if isinstance(value, int):
        return (_normalize_non_negative_int("block_ids", value),)
    if isinstance(value, Mapping):
        nested = _field(value, ("block_ids", "block_id", "blocks"))
        return () if nested is None else _coerce_block_ids(nested)
    if isinstance(value, (str, bytes, bytearray, memoryview)):
        raise SchedulerMetadataError("block_ids must not be a string or bytes value")

    result: list[int] = []
    try:
        iterator = iter(value)  # type: ignore[arg-type]
    except TypeError as exc:
        block_id = _field(value, ("block_id", "id"))
        if block_id is None:
            raise SchedulerMetadataError("block_ids must be iterable") from exc
        return _coerce_block_ids(block_id)
    for item in iterator:
        if isinstance(item, Mapping) or not isinstance(item, (int, bool)):
            nested = _field(item, ("block_id", "id"))
            if nested is not None:
                item = nested
        result.append(_normalize_non_negative_int("block_ids", item))
    if len(set(result)) != len(result):
        raise SchedulerMetadataError("block_ids must be unique")
    return tuple(result)


def _normalize_block_id(block_id: int, *, num_blocks: int) -> int:
    block = _normalize_non_negative_int("block_ids", block_id)
    if block >= num_blocks:
        raise CPUStagingSerializationError(
            f"block_id {block} is out of range for {num_blocks} blocks"
        )
    return block


def _normalize_non_negative_int(name: str, value: object) -> int:
    if isinstance(value, bool):
        raise SchedulerMetadataError(f"{name} entries must be integers")
    try:
        coerced = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise SchedulerMetadataError(f"{name} entries must be integers") from exc
    if coerced < 0:
        raise SchedulerMetadataError(f"{name} entries must be non-negative")
    return coerced


def _field(value: object | None, names: Iterable[str]) -> object | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return None
    for name in names:
        try:
            return getattr(value, name)
        except AttributeError:
            continue
    return None


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _wrap_put_error(exc: Exception) -> Exception:
    class_name = exc.__class__.__name__
    if class_name in {
        "BifrostClosedError",
        "BifrostConnectionError",
        "BifrostTimeoutError",
    }:
        return DaemonUnavailableError(f"BIFROST daemon unavailable: {exc}")
    if class_name.startswith("Bifrost"):
        return StoreCommitError(f"BIFROST PUT failed: {exc}")
    return StoreCommitError(f"BIFROST PUT failed: {exc}")


__all__ = ["SaveResult", "VLLMSavePath"]
