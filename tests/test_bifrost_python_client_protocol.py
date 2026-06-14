from __future__ import annotations

import struct
import sys
from io import BytesIO
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BIFROST_PY = REPO_ROOT / "bifrost_py"
if str(BIFROST_PY) not in sys.path:
    sys.path.insert(0, str(BIFROST_PY))

from bifrost_client.errors import BifrostProtocolError, BifrostServerError
from bifrost_client.protocol import (
    TRANSPORT_VERSION,
    decode_frame,
    encode_frame,
    frame_header,
    raise_for_error_frame,
    read_frame,
)


def test_encode_decode_frame_matches_rust_hello_example() -> None:
    header = frame_header(
        "hello",
        "hello-1",
        0,
        peer_role="client",
        supported_versions=[TRANSPORT_VERSION],
    )

    encoded = encode_frame(header)
    expected_header = (
        b'{"version":"bifrost.transport.v1alpha1","type":"hello",'
        b'"transfer_id":"hello-1","payload_len":0,"peer_role":"client",'
        b'"supported_versions":["bifrost.transport.v1alpha1"]}'
    )

    assert encoded == struct.pack(">I", len(expected_header)) + expected_header
    decoded = decode_frame(encoded)
    assert decoded.header == header
    assert decoded.payload == b""


def test_encode_decode_frame_matches_rust_chunk_example() -> None:
    payload = b"abcdef"
    header = frame_header(
        "chunk",
        "put-1",
        len(payload),
        object_id="bifrost://object/blake3/" + "a" * 64,
        chunk_index=0,
        total_chunks=1,
        chunk_offset=0,
        object_payload_len=len(payload),
        payload_hash="blake3:" + "b" * 64,
    )

    frame = read_frame(BytesIO(encode_frame(header, payload)))

    assert frame.header == header
    assert frame.payload == payload


def test_payload_length_mismatch_rejects() -> None:
    header = frame_header("query_request", "query-1", 5)
    header_bytes = b'{"version":"bifrost.transport.v1alpha1","type":"query_request","transfer_id":"query-1","payload_len":5}'
    encoded = struct.pack(">I", len(header_bytes)) + header_bytes + b"abc"

    with pytest.raises(BifrostProtocolError, match="payload length mismatch"):
        decode_frame(encoded)


def test_error_frame_raises_server_error() -> None:
    header = frame_header(
        "error",
        "put-1",
        0,
        status="rejected",
        reason="expected hello as first frame",
    )
    frame = decode_frame(encode_frame(header))

    with pytest.raises(BifrostServerError, match="expected hello"):
        raise_for_error_frame(frame)
