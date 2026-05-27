use crate::cache::compute_payload_hash;
use crate::transport::{TransportError, TransportResult};
use serde::{Deserialize, Serialize};

pub const DEFAULT_CHUNK_SIZE: usize = 256 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChunkSpec {
    pub chunk_size: usize,
}

impl ChunkSpec {
    pub fn new(chunk_size: usize) -> TransportResult<Self> {
        if chunk_size == 0 {
            return Err(TransportError::Protocol(
                "chunk_size must be greater than zero".to_string(),
            ));
        }
        Ok(Self { chunk_size })
    }
}

impl Default for ChunkSpec {
    fn default() -> Self {
        Self {
            chunk_size: DEFAULT_CHUNK_SIZE,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChunkInfo {
    pub chunk_index: u64,
    pub offset: u64,
    pub len: u64,
    pub hash: String,
}

impl ChunkInfo {
    pub fn verify(&self, bytes: &[u8]) -> TransportResult<()> {
        if self.len != bytes.len() as u64 {
            return Err(TransportError::Protocol(format!(
                "chunk {} length mismatch: expected {} bytes, got {} bytes",
                self.chunk_index,
                self.len,
                bytes.len()
            )));
        }

        let actual = compute_payload_hash(bytes);
        if self.hash != actual {
            return Err(TransportError::Protocol(format!(
                "chunk {} hash mismatch",
                self.chunk_index
            )));
        }

        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChunkManifest {
    pub object_id: Option<String>,
    pub payload_len: u64,
    pub payload_hash: String,
    pub chunk_size: usize,
    pub total_chunks: u64,
    pub chunks: Vec<ChunkInfo>,
}

impl ChunkManifest {
    pub fn validate_shape(&self) -> TransportResult<()> {
        if self.payload_len == 0 {
            return Err(TransportError::Protocol(
                "payload_len must be greater than zero".to_string(),
            ));
        }
        ChunkSpec::new(self.chunk_size)?;
        if self.total_chunks == 0 {
            return Err(TransportError::Protocol(
                "total_chunks must be greater than zero".to_string(),
            ));
        }
        if self.total_chunks as usize != self.chunks.len() {
            return Err(TransportError::Protocol(format!(
                "manifest chunk count mismatch: total_chunks={}, chunks={}",
                self.total_chunks,
                self.chunks.len()
            )));
        }

        let mut expected_offset = 0_u64;
        for (expected_index, chunk) in self.chunks.iter().enumerate() {
            if chunk.chunk_index != expected_index as u64 {
                return Err(TransportError::Protocol(format!(
                    "chunk index mismatch at manifest position {}",
                    expected_index
                )));
            }
            if chunk.offset != expected_offset {
                return Err(TransportError::Protocol(format!(
                    "chunk {} offset mismatch: expected {}, got {}",
                    chunk.chunk_index, expected_offset, chunk.offset
                )));
            }
            if chunk.len == 0 {
                return Err(TransportError::Protocol(format!(
                    "chunk {} length must be greater than zero",
                    chunk.chunk_index
                )));
            }

            let remaining = self.payload_len - expected_offset;
            let expected_len = remaining.min(self.chunk_size as u64);
            if chunk.len != expected_len {
                return Err(TransportError::Protocol(format!(
                    "chunk {} length mismatch: expected {}, got {}",
                    chunk.chunk_index, expected_len, chunk.len
                )));
            }
            expected_offset += chunk.len;
        }

        if expected_offset != self.payload_len {
            return Err(TransportError::Protocol(format!(
                "manifest payload length mismatch: expected {}, got {}",
                self.payload_len, expected_offset
            )));
        }

        Ok(())
    }
}
