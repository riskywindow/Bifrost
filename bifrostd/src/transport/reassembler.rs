use crate::cache::compute_payload_hash;
use crate::transport::manifest::{ChunkInfo, ChunkManifest};
use crate::transport::{TransportError, TransportResult};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChunkAcceptStatus {
    Accepted,
    Duplicate,
}

#[derive(Debug, Clone)]
pub struct Reassembler {
    manifest: ChunkManifest,
    chunks: Vec<Option<Vec<u8>>>,
    received_chunks: usize,
}

impl Reassembler {
    pub fn new(manifest: ChunkManifest) -> TransportResult<Self> {
        manifest.validate_shape()?;
        Ok(Self {
            chunks: vec![None; manifest.total_chunks as usize],
            manifest,
            received_chunks: 0,
        })
    }

    pub fn manifest(&self) -> &ChunkManifest {
        &self.manifest
    }

    pub fn received_chunk_count(&self) -> usize {
        self.received_chunks
    }

    pub fn is_complete(&self) -> bool {
        self.received_chunks == self.manifest.total_chunks as usize
    }

    pub fn accept_chunk(
        &mut self,
        chunk_index: u64,
        bytes: &[u8],
    ) -> TransportResult<ChunkAcceptStatus> {
        let info = self
            .manifest
            .chunks
            .get(chunk_index as usize)
            .cloned()
            .ok_or_else(|| {
                TransportError::Protocol(format!("chunk index {} is out of range", chunk_index))
            })?;
        self.accept_chunk_info(&info, bytes)
    }

    pub fn accept_chunk_info(
        &mut self,
        info: &ChunkInfo,
        bytes: &[u8],
    ) -> TransportResult<ChunkAcceptStatus> {
        let expected = self
            .manifest
            .chunks
            .get(info.chunk_index as usize)
            .ok_or_else(|| {
                TransportError::Protocol(format!(
                    "chunk index {} is out of range",
                    info.chunk_index
                ))
            })?;

        if info != expected {
            return Err(TransportError::Protocol(format!(
                "chunk {} metadata mismatch",
                info.chunk_index
            )));
        }

        let slot = self
            .chunks
            .get_mut(info.chunk_index as usize)
            .expect("manifest index was checked above");
        match slot {
            Some(existing) if existing.as_slice() == bytes => Ok(ChunkAcceptStatus::Duplicate),
            Some(_) => Err(TransportError::Protocol(format!(
                "chunk {} conflicts with previously accepted bytes",
                info.chunk_index
            ))),
            None => {
                info.verify(bytes)?;
                *slot = Some(bytes.to_vec());
                self.received_chunks += 1;
                Ok(ChunkAcceptStatus::Accepted)
            }
        }
    }

    pub fn finish(&self) -> TransportResult<Vec<u8>> {
        if !self.is_complete() {
            return Err(TransportError::Protocol(format!(
                "reassembly incomplete: received {} of {} chunks",
                self.received_chunks, self.manifest.total_chunks
            )));
        }

        let mut payload = Vec::with_capacity(self.manifest.payload_len as usize);
        for (index, chunk) in self.chunks.iter().enumerate() {
            let bytes = chunk
                .as_ref()
                .ok_or_else(|| TransportError::Protocol(format!("chunk {} is missing", index)))?;
            payload.extend_from_slice(bytes);
        }

        if payload.len() as u64 != self.manifest.payload_len {
            return Err(TransportError::Protocol(format!(
                "reassembled payload length mismatch: expected {}, got {}",
                self.manifest.payload_len,
                payload.len()
            )));
        }

        let actual_hash = compute_payload_hash(&payload);
        if actual_hash != self.manifest.payload_hash {
            return Err(TransportError::Protocol(
                "reassembled payload hash mismatch".to_string(),
            ));
        }

        Ok(payload)
    }
}
