use super::{SpoolResult, StagingPaths};
use crate::transport::{ChunkManifest, Reassembler};
use std::fs;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StagedPayload {
    pub bytes: Vec<u8>,
    pub manifest: ChunkManifest,
}

pub fn assemble_staged_payload(paths: &StagingPaths) -> SpoolResult<StagedPayload> {
    let manifest: ChunkManifest = serde_json::from_slice(&fs::read(&paths.manifest)?)?;
    let mut reassembler = Reassembler::new(manifest.clone())?;

    for chunk in &manifest.chunks {
        let bytes = fs::read(paths.chunk_path(chunk.chunk_index))?;
        reassembler.accept_chunk_info(chunk, &bytes)?;
    }

    Ok(StagedPayload {
        bytes: reassembler.finish()?,
        manifest,
    })
}
