use crate::cache::compute_payload_hash;
use crate::transport::manifest::{ChunkInfo, ChunkManifest, ChunkSpec};
use crate::transport::{TransportError, TransportResult};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Chunk<'a> {
    pub info: ChunkInfo,
    pub bytes: &'a [u8],
}

pub fn chunk_bytes(payload: &[u8], chunk_size: usize) -> TransportResult<ChunkManifest> {
    let chunks = iter_chunks(payload, chunk_size)?
        .into_iter()
        .map(|chunk| chunk.info)
        .collect::<Vec<_>>();

    Ok(ChunkManifest {
        object_id: None,
        payload_len: payload.len() as u64,
        payload_hash: compute_payload_hash(payload),
        chunk_size,
        total_chunks: chunks.len() as u64,
        chunks,
    })
}

pub fn iter_chunks(payload: &[u8], chunk_size: usize) -> TransportResult<Vec<Chunk<'_>>> {
    ChunkSpec::new(chunk_size)?;
    if payload.is_empty() {
        return Err(TransportError::Protocol(
            "payload must be greater than zero bytes".to_string(),
        ));
    }

    let mut chunks = Vec::with_capacity(payload.len().div_ceil(chunk_size));
    for (index, bytes) in payload.chunks(chunk_size).enumerate() {
        let offset = index * chunk_size;
        chunks.push(Chunk {
            info: ChunkInfo {
                chunk_index: index as u64,
                offset: offset as u64,
                len: bytes.len() as u64,
                hash: compute_payload_hash(bytes),
            },
            bytes,
        });
    }

    Ok(chunks)
}
