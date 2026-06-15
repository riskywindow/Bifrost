"""Async Python client for the BIFROST TCP daemon protocol."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import replace
from typing import Any

from bifrost_kv.hashing import compute_payload_hash
from bifrost_kv.validate import validate_object

from .errors import (
    BifrostClientError,
    BifrostClosedError,
    BifrostConnectionError,
    BifrostNotFoundError,
    BifrostProtocolError,
    BifrostServerError,
    BifrostTimeoutError,
    BifrostValidationError,
)
from .models import BifrostClientConfig, ObjectSummary, PutResult, StoreStats, StoredObject
from .protocol import (
    TRANSPORT_VERSION,
    Frame,
    compact_json_bytes,
    frame_header,
    raise_for_error_frame,
    read_async_frame,
    write_async_frame,
)


class BifrostAsyncClient:
    """Async one-request-per-connection client for `bifrost-daemon`."""

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        config: BifrostClientConfig | None = None,
    ) -> None:
        if config is None:
            config = BifrostClientConfig(endpoint=endpoint or BifrostClientConfig.endpoint)
        elif endpoint is not None:
            config = BifrostClientConfig(
                endpoint=endpoint,
                timeout_seconds=config.timeout_seconds,
                default_chunk_size=config.default_chunk_size,
            )
        self.config = config
        self._closed = False
        self._connected = False

    async def connect(self) -> "BifrostAsyncClient":
        self._closed = False
        await self.ping()
        self._connected = True
        return self

    async def close(self) -> None:
        self._closed = True
        self._connected = False

    async def ping(self) -> bool:
        self._ensure_open()
        reader, writer, transfer_id = await self._open_handshaken("ping")
        del reader
        await self._close_writer(writer)
        self._connected = True
        return bool(transfer_id)

    async def put_object(
        self,
        metadata: dict[str, Any],
        payload: bytes,
        chunk_size: int = 256 * 1024,
    ) -> PutResult:
        self._ensure_open()
        if not payload:
            raise BifrostValidationError("payload must be greater than zero bytes")
        if chunk_size <= 0:
            raise BifrostValidationError("chunk_size must be greater than zero")

        validation = validate_object(metadata, payload, None)
        if validation.status != "accepted":
            raise BifrostValidationError(validation.reason_code)
        object_id = validation.object_id
        if not object_id:
            raise BifrostValidationError("accepted validation missing object_id")

        descriptor = compact_json_bytes(metadata)
        manifest = _chunk_manifest(object_id, payload, chunk_size)
        reader, writer, transfer_id = await self._open_handshaken("put")
        try:
            begin = frame_header(
                "put_begin",
                transfer_id,
                len(descriptor),
                object_id=object_id,
                descriptor_len=len(descriptor),
                object_payload_len=len(payload),
                chunk_size=chunk_size,
                total_chunks=manifest["total_chunks"],
                target_profile_id="none",
                payload_hash=manifest["payload_hash"],
                flags={"chunk_manifest": manifest},
            )
            await self._write_frame(writer, begin, descriptor)

            for chunk in _iter_chunks(payload, chunk_size):
                chunk_payload = chunk["bytes"]
                header = frame_header(
                    "chunk",
                    transfer_id,
                    len(chunk_payload),
                    object_id=object_id,
                    chunk_index=chunk["chunk_index"],
                    total_chunks=manifest["total_chunks"],
                    chunk_offset=chunk["offset"],
                    object_payload_len=len(chunk_payload),
                    payload_hash=chunk["hash"],
                )
                await self._write_frame(writer, header, chunk_payload)
                ack = await self._read_frame(reader)
                raise_for_error_frame(ack)
                if ack.header.get("type") != "chunk_ack":
                    raise BifrostProtocolError(f"expected chunk_ack, got {ack.header.get('type')}")
                status = ack.header.get("status")
                if status not in {"accepted", "duplicate"}:
                    raise BifrostServerError(str(ack.header.get("reason") or "chunk_rejected"))

            commit = frame_header(
                "put_commit",
                transfer_id,
                0,
                object_id=object_id,
                total_chunks=manifest["total_chunks"],
                object_payload_len=len(payload),
            )
            await self._write_frame(writer, commit)
            result = await self._read_frame(reader)
            raise_for_error_frame(result)
            if result.header.get("type") != "put_result":
                raise BifrostProtocolError(f"expected put_result, got {result.header.get('type')}")
            status = str(result.header.get("status") or "rejected")
            reason = str(result.header.get("reason") or "")
            if status != "committed":
                raise BifrostServerError(reason or "put_rejected")
            integrity = metadata.get("integrity") if isinstance(metadata.get("integrity"), dict) else {}
            return PutResult(
                object_id=object_id,
                payload_hash=integrity.get("payload_hash"),
                descriptor_hash=integrity.get("descriptor_hash"),
                stored=True,
                verified=True,
                reason=reason,
            )
        finally:
            await self._close_writer(writer)

    async def put_objects(
        self,
        items: list[tuple[dict[str, Any], bytes]],
        chunk_size: int = 256 * 1024,
    ) -> list[PutResult]:
        return [
            await self.put_object(metadata, payload, chunk_size)
            for metadata, payload in items
        ]

    async def has_object(self, object_id: str) -> bool:
        self._ensure_open()
        reader, writer, transfer_id = await self._open_handshaken("has")
        try:
            request = frame_header("has_request", transfer_id, 0, object_id=object_id)
            await self._write_frame(writer, request)
            result = await self._read_frame(reader)
            raise_for_error_frame(result)
            if result.header.get("type") != "has_result":
                raise BifrostProtocolError(f"expected has_result, got {result.header.get('type')}")
            return bool(result.header.get("present"))
        finally:
            await self._close_writer(writer)

    async def has_objects(self, object_ids: list[str]) -> list[bool]:
        return [await self.has_object(object_id) for object_id in object_ids]

    async def get_object(self, object_id: str) -> StoredObject:
        self._ensure_open()
        reader, writer, transfer_id = await self._open_handshaken("get")
        try:
            request = frame_header(
                "get_begin",
                transfer_id,
                0,
                object_id=object_id,
                chunk_size=self.config.default_chunk_size,
            )
            await self._write_frame(writer, request)
            first = await self._read_frame(reader)
            raise_for_error_frame(first)
            if first.header.get("type") != "get_result":
                raise BifrostProtocolError(f"expected get_result, got {first.header.get('type')}")
            status = str(first.header.get("status") or "rejected")
            if status != "found":
                raise BifrostNotFoundError(str(first.header.get("reason") or "not_found"))

            manifest = _manifest_from_header(first.header)
            metadata_bytes = first.payload
            chunks: list[bytes | None] = [None] * int(manifest["total_chunks"])
            for _ in range(int(manifest["total_chunks"])):
                frame = await self._read_frame(reader)
                raise_for_error_frame(frame)
                if frame.header.get("type") != "chunk":
                    raise BifrostProtocolError(f"expected chunk, got {frame.header.get('type')}")
                index = int(frame.header["chunk_index"])
                if index < 0 or index >= len(chunks):
                    raise BifrostProtocolError("chunk index out of range")
                expected_hash = str(frame.header.get("payload_hash") or "")
                actual_hash = compute_payload_hash(frame.payload)
                if expected_hash != actual_hash:
                    raise BifrostValidationError("chunk payload hash mismatch")
                chunks[index] = frame.payload

            done = await self._read_frame(reader)
            raise_for_error_frame(done)
            if done.header.get("type") != "get_result" or done.header.get("status") != "success":
                raise BifrostProtocolError(str(done.header.get("reason") or "GET did not finish"))
            if any(chunk is None for chunk in chunks):
                raise BifrostProtocolError("GET returned incomplete chunk set")
            payload = b"".join(chunk for chunk in chunks if chunk is not None)
            if compute_payload_hash(payload) != manifest["payload_hash"]:
                raise BifrostValidationError("payload hash mismatch")
            metadata = json.loads(metadata_bytes.decode("utf-8"))
            validation = validate_object(metadata, payload, None)
            if validation.status != "accepted":
                raise BifrostValidationError(validation.reason_code)
            if validation.object_id != object_id:
                raise BifrostValidationError("object_id_mismatch")
            integrity = metadata.get("integrity") if isinstance(metadata.get("integrity"), dict) else {}
            return StoredObject(
                object_id=object_id,
                metadata=metadata,
                payload=payload,
                payload_hash=integrity.get("payload_hash"),
                descriptor_hash=integrity.get("descriptor_hash"),
            )
        finally:
            await self._close_writer(writer)

    async def get_objects(self, object_ids: list[str]) -> list[StoredObject]:
        return [await self.get_object(object_id) for object_id in object_ids]

    async def query_by_opaque_key_hash(
        self,
        engine_name: str,
        integration_name: str,
        opaque_engine_key_hash: str,
    ) -> list[ObjectSummary]:
        objects = await self.query_objects(
            engine_name=engine_name,
            opaque_engine_key_hash=opaque_engine_key_hash,
        )
        filtered: list[ObjectSummary] = []
        for summary in objects:
            try:
                stored = await self.get_object(summary.object_id)
            except BifrostClientError:
                continue
            engine = stored.metadata.get("engine_profile")
            if isinstance(engine, dict) and engine.get("integration_name") == integration_name:
                filtered.append(replace(summary, integration_name=integration_name))
        return filtered

    async def query_by_opaque_key_hashes(
        self,
        engine_name: str,
        integration_name: str,
        opaque_engine_key_hashes: list[str],
    ) -> dict[str, list[ObjectSummary]]:
        return {
            key_hash: await self.query_by_opaque_key_hash(
                engine_name,
                integration_name,
                key_hash,
            )
            for key_hash in opaque_engine_key_hashes
        }

    async def list_objects(
        self,
        *,
        state: str | None = None,
        model_hash: str | None = None,
        prefix_hash: str | None = None,
        engine_name: str | None = None,
        opaque_engine_key_hash: str | None = None,
        layer_id: int | None = None,
        kv_block_id: int | None = None,
        limit: int | None = None,
    ) -> list[ObjectSummary]:
        return await self._list_or_query(
            "list_request",
            "list_result",
            state=state,
            model_hash=model_hash,
            prefix_hash=prefix_hash,
            engine_name=engine_name,
            opaque_engine_key_hash=opaque_engine_key_hash,
            layer_id=layer_id,
            kv_block_id=kv_block_id,
            limit=limit,
        )

    async def query_objects(self, **filters: Any) -> list[ObjectSummary]:
        return await self._list_or_query("query_request", "query_result", **filters)

    async def stats(self) -> StoreStats:
        self._ensure_open()
        frame = await self._json_request("stats_request", "stats_result", {})
        if frame.header.get("status") != "ok":
            raise BifrostServerError(str(frame.header.get("reason") or "store_stats_failed"))
        return StoreStats(**json.loads(frame.payload.decode("utf-8")))

    async def _list_or_query(
        self,
        request_type: str,
        response_type: str,
        **filters: Any,
    ) -> list[ObjectSummary]:
        payload_filter = {key: value for key, value in filters.items() if value is not None}
        frame = await self._json_request(request_type, response_type, payload_filter)
        if frame.header.get("status") != "ok":
            raise BifrostServerError(str(frame.header.get("reason") or "store_query_failed"))
        response = json.loads(frame.payload.decode("utf-8"))
        return [_summary_from_wire(item) for item in response.get("objects", [])]

    async def _json_request(
        self,
        request_type: str,
        response_type: str,
        value: dict[str, Any],
    ) -> Frame:
        self._ensure_open()
        payload = b"" if request_type == "stats_request" else compact_json_bytes(value)
        reader, writer, transfer_id = await self._open_handshaken("store")
        try:
            await self._write_frame(
                writer,
                frame_header(request_type, transfer_id, len(payload)),
                payload,
            )
            frame = await self._read_frame(reader)
            raise_for_error_frame(frame)
            if frame.header.get("type") != response_type:
                raise BifrostProtocolError(
                    f"expected {response_type}, got {frame.header.get('type')}"
                )
            return frame
        finally:
            await self._close_writer(writer)

    async def _open_handshaken(
        self, prefix: str
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, str]:
        host, port = _split_endpoint(self.config.endpoint)
        transfer_id = _new_transfer_id(prefix)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=self.config.timeout_seconds,
            )
            hello = frame_header(
                "hello",
                transfer_id,
                0,
                peer_role="client",
                supported_versions=[TRANSPORT_VERSION],
                flags={"hello": {"role": "client"}},
            )
            await self._write_frame(writer, hello)
            response = await self._read_frame(reader)
            raise_for_error_frame(response)
            if response.header.get("type") != "hello":
                raise BifrostProtocolError(f"expected daemon hello, got {response.header.get('type')}")
            return reader, writer, transfer_id
        except asyncio.TimeoutError as exc:
            raise BifrostTimeoutError("timed out connecting to daemon") from exc
        except OSError as exc:
            raise BifrostConnectionError(str(exc)) from exc

    async def _read_frame(self, reader: asyncio.StreamReader) -> Frame:
        try:
            return await asyncio.wait_for(
                read_async_frame(reader),
                timeout=self.config.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise BifrostTimeoutError("timed out waiting for daemon frame") from exc

    async def _write_frame(
        self,
        writer: asyncio.StreamWriter,
        header: dict[str, Any],
        payload: bytes = b"",
    ) -> None:
        try:
            await asyncio.wait_for(
                write_async_frame(writer, header, payload),
                timeout=self.config.timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise BifrostTimeoutError("timed out writing daemon frame") from exc
        except OSError as exc:
            raise BifrostConnectionError(str(exc)) from exc

    async def _close_writer(self, writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=self.config.timeout_seconds)
        except (asyncio.TimeoutError, OSError):
            pass

    def _ensure_open(self) -> None:
        if self._closed:
            raise BifrostClosedError("BIFROST client is closed")


def _split_endpoint(endpoint: str) -> tuple[str, int]:
    if endpoint.startswith("tcp://"):
        endpoint = endpoint[len("tcp://") :]
    if endpoint.startswith("bifrost+tcp://"):
        endpoint = endpoint[len("bifrost+tcp://") :]
    host, sep, port_text = endpoint.rpartition(":")
    if not sep or not host:
        raise BifrostConnectionError(f"invalid endpoint: {endpoint}")
    try:
        return host, int(port_text)
    except ValueError as exc:
        raise BifrostConnectionError(f"invalid endpoint port: {endpoint}") from exc


def _new_transfer_id(prefix: str) -> str:
    return f"{prefix}-{os.getpid()}-{time.time_ns()}"


def _iter_chunks(payload: bytes, chunk_size: int) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for index, offset in enumerate(range(0, len(payload), chunk_size)):
        chunk = payload[offset : offset + chunk_size]
        chunks.append(
            {
                "chunk_index": index,
                "offset": offset,
                "len": len(chunk),
                "hash": compute_payload_hash(chunk),
                "bytes": chunk,
            }
        )
    return chunks


def _chunk_manifest(object_id: str, payload: bytes, chunk_size: int) -> dict[str, Any]:
    chunks = [
        {key: value for key, value in chunk.items() if key != "bytes"}
        for chunk in _iter_chunks(payload, chunk_size)
    ]
    return {
        "object_id": object_id,
        "payload_len": len(payload),
        "payload_hash": compute_payload_hash(payload),
        "chunk_size": chunk_size,
        "total_chunks": len(chunks),
        "chunks": chunks,
    }


def _manifest_from_header(header: dict[str, Any]) -> dict[str, Any]:
    flags = header.get("flags")
    if not isinstance(flags, dict) or not isinstance(flags.get("chunk_manifest"), dict):
        raise BifrostProtocolError("get_result missing chunk_manifest")
    manifest = flags["chunk_manifest"]
    if int(manifest.get("payload_len", -1)) <= 0:
        raise BifrostProtocolError("invalid chunk_manifest payload_len")
    return manifest


def _summary_from_wire(item: dict[str, Any]) -> ObjectSummary:
    return ObjectSummary(
        object_id=item["object_id"],
        object_type=item["object_type"],
        state=item["state"],
        byte_length=int(item["byte_length"]),
        model_hash=item.get("model_hash"),
        prefix_hash=item.get("prefix_hash"),
        engine_name=item.get("engine_name"),
        integration_name=item.get("integration_name"),
        opaque_engine_key_hash=item.get("opaque_engine_key_hash"),
        layer_id=item.get("layer_id"),
        kv_block_id=item.get("kv_block_id"),
        pin_count=int(item.get("pin_count") or 0),
        last_accessed_unix_ms=item.get("last_accessed_unix_ms"),
    )
