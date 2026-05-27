use bifrostd::cache::compute_payload_hash;
use bifrostd::transport::{
    chunk_bytes, iter_chunks, ChunkAcceptStatus, Reassembler, TransportError,
};

fn deterministic_payload(len: usize) -> Vec<u8> {
    (0..len).map(|index| (index % 251) as u8).collect()
}

#[test]
fn chunking_deterministic_payload_produces_expected_total_chunks() {
    let payload = deterministic_payload(10);
    let first = chunk_bytes(&payload, 4).unwrap();
    let second = chunk_bytes(&payload, 4).unwrap();

    assert_eq!(first.total_chunks, 3);
    assert_eq!(first.chunks, second.chunks);
    assert_eq!(first.payload_hash, second.payload_hash);
    assert_eq!(first.payload_hash, compute_payload_hash(&payload));
}

#[test]
fn last_chunk_size_is_correct() {
    let payload = deterministic_payload(10);
    let manifest = chunk_bytes(&payload, 4).unwrap();

    assert_eq!(manifest.chunks[0].len, 4);
    assert_eq!(manifest.chunks[1].len, 4);
    assert_eq!(manifest.chunks[2].len, 2);
}

#[test]
fn each_chunk_hash_verifies() {
    let payload = deterministic_payload(10);
    let chunks = iter_chunks(&payload, 4).unwrap();

    for chunk in chunks {
        chunk.info.verify(chunk.bytes).unwrap();
    }
}

#[test]
fn zero_chunk_size_is_rejected() {
    let payload = deterministic_payload(10);
    let err = chunk_bytes(&payload, 0).unwrap_err();

    assert!(matches!(err, TransportError::Protocol(message) if message.contains("chunk_size")));
}

#[test]
fn empty_payload_is_rejected() {
    let err = chunk_bytes(&[], 4).unwrap_err();

    assert!(matches!(err, TransportError::Protocol(message) if message.contains("payload")));
}

#[test]
fn one_chunk_payload_is_supported() {
    let payload = deterministic_payload(3);
    let manifest = chunk_bytes(&payload, 4).unwrap();

    assert_eq!(manifest.total_chunks, 1);
    assert_eq!(manifest.chunks[0].offset, 0);
    assert_eq!(manifest.chunks[0].len, 3);
}

#[test]
fn exact_boundary_payload_has_no_empty_trailing_chunk() {
    let payload = deterministic_payload(8);
    let manifest = chunk_bytes(&payload, 4).unwrap();

    assert_eq!(manifest.total_chunks, 2);
    assert_eq!(manifest.chunks[0].len, 4);
    assert_eq!(manifest.chunks[1].len, 4);
}

#[test]
fn reassembler_accepts_chunks_in_order() {
    let payload = deterministic_payload(10);
    let manifest = chunk_bytes(&payload, 4).unwrap();
    let chunks = iter_chunks(&payload, 4).unwrap();
    let mut reassembler = Reassembler::new(manifest).unwrap();

    for chunk in &chunks {
        assert_eq!(
            reassembler
                .accept_chunk_info(&chunk.info, chunk.bytes)
                .unwrap(),
            ChunkAcceptStatus::Accepted
        );
    }

    assert!(reassembler.is_complete());
    assert_eq!(reassembler.received_chunk_count(), 3);
    assert_eq!(reassembler.finish().unwrap(), payload);
}

#[test]
fn reassembler_accepts_chunks_out_of_order() {
    let payload = deterministic_payload(10);
    let manifest = chunk_bytes(&payload, 4).unwrap();
    let chunks = iter_chunks(&payload, 4).unwrap();
    let mut reassembler = Reassembler::new(manifest).unwrap();

    for index in [2_usize, 0, 1] {
        let chunk = &chunks[index];
        reassembler
            .accept_chunk_info(&chunk.info, chunk.bytes)
            .unwrap();
    }

    assert!(reassembler.is_complete());
    assert_eq!(reassembler.finish().unwrap(), payload);
}

#[test]
fn reassembler_rejects_corrupted_chunk_bytes() {
    let payload = deterministic_payload(10);
    let manifest = chunk_bytes(&payload, 4).unwrap();
    let chunks = iter_chunks(&payload, 4).unwrap();
    let mut reassembler = Reassembler::new(manifest).unwrap();
    let mut corrupted = chunks[0].bytes.to_vec();
    corrupted[0] ^= 0xff;

    let err = reassembler
        .accept_chunk_info(&chunks[0].info, &corrupted)
        .unwrap_err();

    assert!(matches!(err, TransportError::Protocol(message) if message.contains("hash mismatch")));
    assert_eq!(reassembler.received_chunk_count(), 0);
}

#[test]
fn reassembler_rejects_wrong_chunk_index() {
    let payload = deterministic_payload(10);
    let manifest = chunk_bytes(&payload, 4).unwrap();
    let mut reassembler = Reassembler::new(manifest).unwrap();

    let err = reassembler.accept_chunk(99, b"bad").unwrap_err();

    assert!(matches!(err, TransportError::Protocol(message) if message.contains("out of range")));
}

#[test]
fn reassembler_rejects_chunk_metadata_mismatch() {
    let payload = deterministic_payload(10);
    let manifest = chunk_bytes(&payload, 4).unwrap();
    let chunks = iter_chunks(&payload, 4).unwrap();
    let mut reassembler = Reassembler::new(manifest).unwrap();
    let mut wrong_offset = chunks[0].info.clone();
    wrong_offset.offset = 1;

    let err = reassembler
        .accept_chunk_info(&wrong_offset, chunks[0].bytes)
        .unwrap_err();

    assert!(
        matches!(err, TransportError::Protocol(message) if message.contains("metadata mismatch"))
    );
}

#[test]
fn reassembler_rejects_conflicting_duplicate_chunk() {
    let payload = deterministic_payload(10);
    let manifest = chunk_bytes(&payload, 4).unwrap();
    let chunks = iter_chunks(&payload, 4).unwrap();
    let mut reassembler = Reassembler::new(manifest).unwrap();
    let mut conflicting = chunks[0].bytes.to_vec();
    conflicting[0] ^= 0xff;

    reassembler
        .accept_chunk_info(&chunks[0].info, chunks[0].bytes)
        .unwrap();
    let err = reassembler
        .accept_chunk_info(&chunks[0].info, &conflicting)
        .unwrap_err();

    assert!(matches!(err, TransportError::Protocol(message) if message.contains("conflicts")));
    assert_eq!(reassembler.received_chunk_count(), 1);
}

#[test]
fn reassembler_allows_identical_duplicate_chunk() {
    let payload = deterministic_payload(10);
    let manifest = chunk_bytes(&payload, 4).unwrap();
    let chunks = iter_chunks(&payload, 4).unwrap();
    let mut reassembler = Reassembler::new(manifest).unwrap();

    assert_eq!(
        reassembler
            .accept_chunk_info(&chunks[0].info, chunks[0].bytes)
            .unwrap(),
        ChunkAcceptStatus::Accepted
    );
    assert_eq!(
        reassembler
            .accept_chunk_info(&chunks[0].info, chunks[0].bytes)
            .unwrap(),
        ChunkAcceptStatus::Duplicate
    );
    assert_eq!(reassembler.received_chunk_count(), 1);
}

#[test]
fn reassembler_rejects_missing_chunk_on_finish() {
    let payload = deterministic_payload(10);
    let manifest = chunk_bytes(&payload, 4).unwrap();
    let chunks = iter_chunks(&payload, 4).unwrap();
    let mut reassembler = Reassembler::new(manifest).unwrap();

    reassembler
        .accept_chunk_info(&chunks[0].info, chunks[0].bytes)
        .unwrap();
    let err = reassembler.finish().unwrap_err();

    assert!(matches!(err, TransportError::Protocol(message) if message.contains("incomplete")));
}

#[test]
fn final_payload_equals_original_payload() {
    let payload = deterministic_payload(1025);
    let manifest = chunk_bytes(&payload, 256).unwrap();
    let chunks = iter_chunks(&payload, 256).unwrap();
    let mut reassembler = Reassembler::new(manifest).unwrap();

    for chunk in chunks {
        reassembler
            .accept_chunk_info(&chunk.info, chunk.bytes)
            .unwrap();
    }

    assert_eq!(reassembler.finish().unwrap(), payload);
}

#[test]
fn final_payload_hash_mismatch_rejects() {
    let payload = deterministic_payload(10);
    let mut manifest = chunk_bytes(&payload, 4).unwrap();
    manifest.payload_hash = compute_payload_hash(b"different payload");
    let chunks = iter_chunks(&payload, 4).unwrap();
    let mut reassembler = Reassembler::new(manifest).unwrap();

    for chunk in chunks {
        reassembler
            .accept_chunk_info(&chunk.info, chunk.bytes)
            .unwrap();
    }

    let err = reassembler.finish().unwrap_err();

    assert!(
        matches!(err, TransportError::Protocol(message) if message.contains("payload hash mismatch"))
    );
}
