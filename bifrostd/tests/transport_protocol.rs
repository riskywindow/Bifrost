use bifrostd::transport::{
    decode_frame, decode_frame_with_limits, encode_frame, DecodeLimits, FrameHeader, FrameType,
    TransportError, TRANSPORT_VERSION,
};
use std::io::Cursor;
use std::process::Command;

fn hello_header() -> FrameHeader {
    let mut header = FrameHeader::new(FrameType::Hello, "transfer-1", 0);
    header.peer_role = Some("client".to_string());
    header.supported_versions = Some(vec![TRANSPORT_VERSION.to_string()]);
    header
}

#[test]
fn hello_frame_round_trip() {
    let header = hello_header();
    let bytes = encode_frame(&header, &[]).unwrap();
    let frame = decode_frame(&mut Cursor::new(bytes)).unwrap();

    assert_eq!(frame.header.frame_type, FrameType::Hello);
    assert_eq!(frame.header.version, TRANSPORT_VERSION);
    assert_eq!(frame.header.transfer_id, "transfer-1");
    assert!(frame.payload.is_empty());
}

#[test]
fn ping_and_pong_frames_round_trip() {
    for frame_type in [FrameType::Ping, FrameType::Pong] {
        let header = FrameHeader::new(frame_type, "transfer-ping", 0);
        let bytes = encode_frame(&header, &[]).unwrap();
        let frame = decode_frame(&mut Cursor::new(bytes)).unwrap();

        assert_eq!(frame.header.frame_type, frame_type);
        assert!(frame.payload.is_empty());
    }
}

#[test]
fn chunk_frame_round_trip_with_payload() {
    let payload = b"payload chunk bytes".to_vec();
    let mut header = FrameHeader::new(FrameType::Chunk, "transfer-2", payload.len() as u64);
    header.object_id = Some("object-1".to_string());
    header.chunk_index = Some(0);
    header.total_chunks = Some(1);
    header.chunk_offset = Some(0);
    header.object_payload_len = Some(payload.len() as u64);
    header.payload_hash = Some("blake3:chunk".to_string());

    let bytes = encode_frame(&header, &payload).unwrap();
    let frame = decode_frame(&mut Cursor::new(bytes)).unwrap();

    assert_eq!(frame.header, header);
    assert_eq!(frame.payload, payload);
}

#[test]
fn unsupported_version_is_rejected() {
    let mut header = hello_header();
    header.version = "bifrost.transport.future".to_string();

    let err = encode_frame(&header, &[]).unwrap_err();
    assert!(
        matches!(err, TransportError::UnsupportedVersion(version) if version == "bifrost.transport.future")
    );
}

#[test]
fn missing_required_chunk_fields_are_rejected() {
    let payload = b"abc";
    let mut header = FrameHeader::new(FrameType::Chunk, "transfer-3", payload.len() as u64);
    header.object_id = Some("object-1".to_string());

    let err = encode_frame(&header, payload).unwrap_err();
    assert!(
        matches!(err, TransportError::InvalidFrame(message) if message.contains("chunk_index"))
    );
}

#[test]
fn payload_length_mismatch_is_rejected() {
    let header = FrameHeader::new(FrameType::Hello, "transfer-4", 3);

    let err = encode_frame(&header, &[]).unwrap_err();
    assert!(matches!(
        err,
        TransportError::PayloadLengthMismatch {
            expected: 3,
            actual: 0
        }
    ));
}

#[test]
fn truncated_payload_length_mismatch_is_rejected_on_decode() {
    let header = FrameHeader::new(FrameType::Chunk, "transfer-5", 4);
    let header_bytes = serde_json::to_vec(&serde_json::json!({
        "version": TRANSPORT_VERSION,
        "type": "chunk",
        "transfer_id": "transfer-5",
        "object_id": "object-1",
        "chunk_index": 0,
        "total_chunks": 1,
        "payload_len": 4,
        "chunk_offset": 0,
        "object_payload_len": 4,
        "payload_hash": "blake3:chunk"
    }))
    .unwrap();
    assert_eq!(header.payload_len, 4);

    let mut bytes = Vec::new();
    bytes.extend_from_slice(&(header_bytes.len() as u32).to_be_bytes());
    bytes.extend_from_slice(&header_bytes);
    bytes.extend_from_slice(b"ab");

    let err = decode_frame(&mut Cursor::new(bytes)).unwrap_err();
    assert!(matches!(
        err,
        TransportError::PayloadLengthMismatch {
            expected: 4,
            actual: 2
        }
    ));
}

#[test]
fn payload_too_large_is_rejected_before_allocation() {
    let header_bytes = serde_json::to_vec(&serde_json::json!({
        "version": TRANSPORT_VERSION,
        "type": "chunk",
        "transfer_id": "transfer-too-large",
        "object_id": "object-1",
        "chunk_index": 0,
        "total_chunks": 1,
        "payload_len": 17,
        "chunk_offset": 0,
        "object_payload_len": 17,
        "payload_hash": "blake3:chunk"
    }))
    .unwrap();
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&(header_bytes.len() as u32).to_be_bytes());
    bytes.extend_from_slice(&header_bytes);

    let err = decode_frame_with_limits(
        &mut Cursor::new(bytes),
        DecodeLimits {
            max_header_len: 1024,
            max_payload_len: 16,
        },
    )
    .unwrap_err();
    assert!(matches!(
        err,
        TransportError::PayloadTooLarge {
            actual: 17,
            max: 16
        }
    ));
}

#[test]
fn unknown_frame_type_is_rejected() {
    let header_bytes = serde_json::to_vec(&serde_json::json!({
        "version": TRANSPORT_VERSION,
        "type": "future_frame",
        "transfer_id": "transfer-unknown",
        "payload_len": 0
    }))
    .unwrap();
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&(header_bytes.len() as u32).to_be_bytes());
    bytes.extend_from_slice(&header_bytes);

    let err = decode_frame(&mut Cursor::new(bytes)).unwrap_err();
    assert!(matches!(err, TransportError::Json(_)));
}

#[test]
fn error_frame_requires_structured_reason() {
    let mut header = FrameHeader::new(FrameType::Error, "transfer-error", 0);
    header.status = Some("rejected".to_string());

    let err = encode_frame(&header, &[]).unwrap_err();
    assert!(matches!(
        err,
        TransportError::InvalidFrame(message) if message.contains("reason")
    ));
}

#[test]
fn header_too_large_is_rejected() {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&(5_u32).to_be_bytes());
    bytes.extend_from_slice(b"{}");

    let err = decode_frame_with_limits(
        &mut Cursor::new(bytes),
        DecodeLimits {
            max_header_len: 4,
            max_payload_len: 16,
        },
    )
    .unwrap_err();
    assert!(matches!(
        err,
        TransportError::HeaderTooLarge { actual: 5, max: 4 }
    ));
}

#[test]
fn daemon_help_works() {
    let output = Command::new(env!("CARGO_BIN_EXE_bifrost-daemon"))
        .arg("--help")
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("--listen"));
    assert!(stdout.contains("--spool"));
}

#[test]
fn xfer_help_works() {
    let output = Command::new(env!("CARGO_BIN_EXE_bifrost-xfer"))
        .arg("--help")
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("put"));
    assert!(stdout.contains("get"));
    assert!(stdout.contains("has"));
}
