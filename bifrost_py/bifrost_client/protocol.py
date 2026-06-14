"""BIFROST transport frame helpers compatible with the Rust daemon."""

from __future__ import annotations

import asyncio
import json
import struct
from dataclasses import dataclass
from typing import Any, BinaryIO

from .errors import BifrostProtocolError, BifrostServerError

TRANSPORT_VERSION = "bifrost.transport.v1alpha1"
DEFAULT_MAX_HEADER_LEN = 64 * 1024
DEFAULT_MAX_PAYLOAD_LEN = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Frame:
    header: dict[str, Any]
    payload: bytes = b""


def compact_json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def frame_header(frame_type: str, transfer_id: str, payload_len: int, **fields: Any) -> dict[str, Any]:
    header: dict[str, Any] = {
        "version": TRANSPORT_VERSION,
        "type": frame_type,
        "transfer_id": transfer_id,
        "payload_len": payload_len,
    }
    header.update({key: value for key, value in fields.items() if value is not None})
    return header


def encode_frame(header: dict[str, Any], payload: bytes = b"") -> bytes:
    validate_header(header, len(payload))
    if len(payload) > DEFAULT_MAX_PAYLOAD_LEN:
        raise BifrostProtocolError("payload too large")
    header_bytes = compact_json_bytes(header)
    if len(header_bytes) > DEFAULT_MAX_HEADER_LEN:
        raise BifrostProtocolError("header too large")
    return struct.pack(">I", len(header_bytes)) + header_bytes + payload


def decode_frame(data: bytes) -> Frame:
    if len(data) < 4:
        raise BifrostProtocolError("frame missing header length")
    header_len = struct.unpack(">I", data[:4])[0]
    if header_len > DEFAULT_MAX_HEADER_LEN:
        raise BifrostProtocolError("header too large")
    header_end = 4 + header_len
    if len(data) < header_end:
        raise BifrostProtocolError("truncated frame header")
    try:
        header = json.loads(data[4:header_end].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BifrostProtocolError(f"invalid frame header: {exc}") from exc
    payload_len = _required_int(header, "payload_len")
    if payload_len > DEFAULT_MAX_PAYLOAD_LEN:
        raise BifrostProtocolError("payload too large")
    payload = data[header_end:]
    if len(payload) != payload_len:
        raise BifrostProtocolError(
            f"payload length mismatch: expected {payload_len}, got {len(payload)}"
        )
    validate_header(header, len(payload))
    return Frame(header=header, payload=payload)


def read_frame(file: BinaryIO) -> Frame:
    header_len_bytes = file.read(4)
    if len(header_len_bytes) != 4:
        raise BifrostProtocolError("frame missing header length")
    header_len = struct.unpack(">I", header_len_bytes)[0]
    if header_len > DEFAULT_MAX_HEADER_LEN:
        raise BifrostProtocolError("header too large")
    header_bytes = file.read(header_len)
    if len(header_bytes) != header_len:
        raise BifrostProtocolError("truncated frame header")
    try:
        header = json.loads(header_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BifrostProtocolError(f"invalid frame header: {exc}") from exc
    payload_len = _required_int(header, "payload_len")
    if payload_len > DEFAULT_MAX_PAYLOAD_LEN:
        raise BifrostProtocolError("payload too large")
    payload = file.read(payload_len)
    if len(payload) != payload_len:
        raise BifrostProtocolError(
            f"payload length mismatch: expected {payload_len}, got {len(payload)}"
        )
    validate_header(header, len(payload))
    return Frame(header=header, payload=payload)


async def read_async_frame(reader: asyncio.StreamReader) -> Frame:
    try:
        header_len_bytes = await reader.readexactly(4)
        header_len = struct.unpack(">I", header_len_bytes)[0]
        if header_len > DEFAULT_MAX_HEADER_LEN:
            raise BifrostProtocolError("header too large")
        header_bytes = await reader.readexactly(header_len)
        header = json.loads(header_bytes.decode("utf-8"))
        payload_len = _required_int(header, "payload_len")
        if payload_len > DEFAULT_MAX_PAYLOAD_LEN:
            raise BifrostProtocolError("payload too large")
        payload = await reader.readexactly(payload_len)
    except asyncio.IncompleteReadError as exc:
        raise BifrostProtocolError("truncated frame") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BifrostProtocolError(f"invalid frame header: {exc}") from exc
    validate_header(header, len(payload))
    return Frame(header=header, payload=payload)


async def write_async_frame(
    writer: asyncio.StreamWriter, header: dict[str, Any], payload: bytes = b""
) -> None:
    writer.write(encode_frame(header, payload))
    await writer.drain()


def raise_for_error_frame(frame: Frame) -> None:
    if frame.header.get("type") == "error":
        reason = str(frame.header.get("reason") or "server_error")
        raise BifrostServerError(reason)


def validate_header(header: dict[str, Any], actual_payload_len: int) -> None:
    if not isinstance(header, dict):
        raise BifrostProtocolError("frame header must be an object")
    if header.get("version") != TRANSPORT_VERSION:
        raise BifrostProtocolError(f"unsupported protocol version: {header.get('version')}")
    frame_type = _required_str(header, "type")
    transfer_id = _required_str(header, "transfer_id")
    if not transfer_id:
        raise BifrostProtocolError("transfer_id is required")
    payload_len = _required_int(header, "payload_len")
    if payload_len != actual_payload_len:
        raise BifrostProtocolError(
            f"payload length mismatch: expected {payload_len}, got {actual_payload_len}"
        )
    if frame_type == "hello":
        _require_zero_payload(payload_len, frame_type)
        _required_str(header, "peer_role")
        versions = header.get("supported_versions")
        if not isinstance(versions, list) or TRANSPORT_VERSION not in versions:
            raise BifrostProtocolError("hello missing supported transport version")
    elif frame_type in {"ping", "pong", "stats_request"}:
        _require_zero_payload(payload_len, frame_type)
    elif frame_type == "error":
        _require_zero_payload(payload_len, frame_type)
        _required_str(header, "status")
        _required_str(header, "reason")
    elif frame_type == "put_begin":
        _required_str(header, "object_id")
        _required_positive_int(header, "total_chunks")
        _required_positive_int(header, "object_payload_len")
        _required_positive_int(header, "chunk_size")
        if _required_int(header, "descriptor_len") != payload_len:
            raise BifrostProtocolError("put_begin descriptor_len mismatch")
        _required_str(header, "target_profile_id")
    elif frame_type == "chunk":
        _required_str(header, "object_id")
        _required_int(header, "chunk_index")
        _required_positive_int(header, "total_chunks")
        _required_int(header, "chunk_offset")
        if _required_positive_int(header, "object_payload_len") != payload_len:
            raise BifrostProtocolError("chunk object_payload_len mismatch")
        _required_str(header, "payload_hash")
    elif frame_type == "chunk_ack":
        _require_zero_payload(payload_len, frame_type)
        _required_str(header, "object_id")
        _required_int(header, "chunk_index")
        status = _required_str(header, "status")
        if status != "accepted" and not str(header.get("reason") or ""):
            raise BifrostProtocolError("chunk_ack reason required")
    elif frame_type == "put_commit":
        _require_zero_payload(payload_len, frame_type)
        _required_str(header, "object_id")
        _required_positive_int(header, "total_chunks")
        _required_positive_int(header, "object_payload_len")
    elif frame_type == "put_result":
        _require_zero_payload(payload_len, frame_type)
        _required_str(header, "object_id")
        status = _required_str(header, "status")
        if status != "committed" and not str(header.get("reason") or ""):
            raise BifrostProtocolError("put_result reason required")
    elif frame_type in {"has_request", "get_begin"}:
        _require_zero_payload(payload_len, frame_type)
        _required_str(header, "object_id")
    elif frame_type == "has_result":
        _require_zero_payload(payload_len, frame_type)
        _required_str(header, "object_id")
        if not isinstance(header.get("present"), bool):
            raise BifrostProtocolError("has_result requires present")
    elif frame_type == "get_result":
        _required_str(header, "object_id")
        status = _required_str(header, "status")
        if status == "found":
            if _required_int(header, "descriptor_len") != payload_len:
                raise BifrostProtocolError("get_result descriptor_len mismatch")
            _required_positive_int(header, "object_payload_len")
            _required_positive_int(header, "chunk_size")
            _required_positive_int(header, "total_chunks")
            _required_str(header, "payload_hash")
        elif status == "success":
            _require_zero_payload(payload_len, frame_type)
            _required_int(header, "descriptor_len")
            _required_int(header, "object_payload_len")
            _required_int(header, "chunk_size")
            _required_int(header, "total_chunks")
            _required_str(header, "payload_hash")
        else:
            _require_zero_payload(payload_len, frame_type)
            _required_int(header, "descriptor_len")
            _required_int(header, "object_payload_len")
            _required_int(header, "chunk_size")
            _required_int(header, "total_chunks")
            _required_str(header, "reason")
    elif frame_type in {"list_request", "query_request"}:
        pass
    elif frame_type in {"list_result", "query_result", "stats_result"}:
        status = _required_str(header, "status")
        if status != "ok" and not str(header.get("reason") or ""):
            raise BifrostProtocolError(f"{frame_type} reason required")
    else:
        raise BifrostProtocolError(f"unknown frame type: {frame_type}")


def _required_str(header: dict[str, Any], key: str) -> str:
    value = header.get(key)
    if not isinstance(value, str) or value == "":
        raise BifrostProtocolError(f"{key} is required")
    return value


def _required_int(header: dict[str, Any], key: str) -> int:
    value = header.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BifrostProtocolError(f"{key} must be a non-negative integer")
    return value


def _required_positive_int(header: dict[str, Any], key: str) -> int:
    value = _required_int(header, key)
    if value <= 0:
        raise BifrostProtocolError(f"{key} must be greater than zero")
    return value


def _require_zero_payload(payload_len: int, frame_type: str) -> None:
    if payload_len != 0:
        raise BifrostProtocolError(f"{frame_type} requires empty payload")
