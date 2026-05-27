use crate::cache::validate_object;
use crate::transport::{
    chunk_bytes, iter_chunks, read_frame, write_frame, ChunkManifest, FrameHeader, FrameType,
    TransportResult, TRANSPORT_VERSION,
};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::net::TcpStream;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PutOutcome {
    pub accepted: bool,
    pub object_id: String,
    pub reason: String,
}

pub async fn put_object(
    endpoint: &str,
    metadata_bytes: Vec<u8>,
    payload: Vec<u8>,
    chunk_size: usize,
    target_profile: Option<Value>,
) -> anyhow::Result<PutOutcome> {
    let metadata: Value = serde_json::from_slice(&metadata_bytes)?;
    let validation = validate_object(&metadata, &payload, target_profile.as_ref());
    if validation.status != "accepted" {
        return Ok(PutOutcome {
            accepted: false,
            object_id: validation.object_id.unwrap_or_default(),
            reason: validation.reason_code,
        });
    }

    let object_id = validation
        .object_id
        .clone()
        .ok_or_else(|| anyhow::anyhow!("local validation accepted without object_id"))?;
    let mut manifest = chunk_bytes(&payload, chunk_size)?;
    manifest.object_id = Some(object_id.clone());
    put_validated_object(endpoint, metadata_bytes, payload, manifest).await
}

pub async fn put_validated_object(
    endpoint: &str,
    metadata_bytes: Vec<u8>,
    payload: Vec<u8>,
    manifest: ChunkManifest,
) -> anyhow::Result<PutOutcome> {
    let object_id = manifest
        .object_id
        .clone()
        .ok_or_else(|| anyhow::anyhow!("PUT manifest requires object_id"))?;
    let transfer_id = new_transfer_id();
    let mut stream = TcpStream::connect(endpoint).await?;

    send_hello(&mut stream, &transfer_id).await?;
    let hello = read_frame(&mut stream).await?;
    if hello.header.frame_type != FrameType::Hello {
        anyhow::bail!("expected daemon hello, got {:?}", hello.header.frame_type);
    }

    let mut begin = FrameHeader::new(
        FrameType::PutBegin,
        transfer_id.clone(),
        metadata_bytes.len() as u64,
    );
    begin.object_id = Some(object_id.clone());
    begin.total_chunks = Some(manifest.total_chunks);
    begin.descriptor_len = Some(metadata_bytes.len() as u64);
    begin.object_payload_len = Some(manifest.payload_len);
    begin.chunk_size = Some(manifest.chunk_size as u64);
    begin.payload_hash = Some(manifest.payload_hash.clone());
    begin.target_profile_id = Some("none".to_string());
    begin.flags = Some(BTreeMap::from([(
        "chunk_manifest".to_string(),
        serde_json::to_value(&manifest)?,
    )]));
    write_frame(&mut stream, &begin, &metadata_bytes).await?;

    for chunk in iter_chunks(&payload, manifest.chunk_size)? {
        let mut header = FrameHeader::new(
            FrameType::Chunk,
            transfer_id.clone(),
            chunk.bytes.len() as u64,
        );
        header.object_id = Some(object_id.clone());
        header.chunk_index = Some(chunk.info.chunk_index);
        header.total_chunks = Some(manifest.total_chunks);
        header.chunk_offset = Some(chunk.info.offset);
        header.object_payload_len = Some(chunk.info.len);
        header.payload_hash = Some(chunk.info.hash.clone());
        write_frame(&mut stream, &header, chunk.bytes).await?;

        let ack = read_frame(&mut stream).await?;
        if ack.header.frame_type != FrameType::ChunkAck {
            anyhow::bail!("expected chunk_ack, got {:?}", ack.header.frame_type);
        }
        let status = ack.header.status.as_deref().unwrap_or("rejected");
        if status != "accepted" && status != "duplicate" {
            let reason = ack
                .header
                .reason
                .unwrap_or_else(|| "chunk_rejected".to_string());
            anyhow::bail!("chunk {} rejected: {}", chunk.info.chunk_index, reason);
        }
    }

    let mut commit = FrameHeader::new(FrameType::PutCommit, transfer_id, 0);
    commit.object_id = Some(object_id.clone());
    commit.total_chunks = Some(manifest.total_chunks);
    commit.object_payload_len = Some(manifest.payload_len);
    write_frame(&mut stream, &commit, &[]).await?;

    let result = read_frame(&mut stream).await?;
    if result.header.frame_type != FrameType::PutResult {
        anyhow::bail!("expected put_result, got {:?}", result.header.frame_type);
    }
    let status = result.header.status.as_deref().unwrap_or("rejected");
    Ok(PutOutcome {
        accepted: status == "committed",
        object_id,
        reason: result.header.reason.unwrap_or_default(),
    })
}

async fn send_hello(stream: &mut TcpStream, transfer_id: &str) -> TransportResult<()> {
    let mut hello = FrameHeader::new(FrameType::Hello, transfer_id, 0);
    hello.peer_role = Some("client".to_string());
    hello.supported_versions = Some(vec![TRANSPORT_VERSION.to_string()]);
    hello.flags = Some(BTreeMap::from([(
        "hello".to_string(),
        json!({"role": "client"}),
    )]));
    write_frame(stream, &hello, &[]).await
}

fn new_transfer_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);
    format!("put-{}-{nanos}", std::process::id())
}
