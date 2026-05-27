use crate::cache::validate_object;
use crate::transport::{
    chunk_bytes, iter_chunks, read_frame, write_frame, ChunkInfo, ChunkManifest, Frame,
    FrameHeader, FrameType, Reassembler, TransportResult, TRANSPORT_VERSION,
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HasOutcome {
    pub present: bool,
    pub object_id: String,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GetOutcome {
    pub found: bool,
    pub object_id: String,
    pub reason: String,
    pub metadata_bytes: Vec<u8>,
    pub payload: Vec<u8>,
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
    let transfer_id = new_transfer_id("put");
    let mut stream = TcpStream::connect(endpoint).await?;

    handshake(&mut stream, &transfer_id).await?;

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

pub async fn has_object(endpoint: &str, object_id: &str) -> anyhow::Result<HasOutcome> {
    let transfer_id = new_transfer_id("has");
    let mut stream = TcpStream::connect(endpoint).await?;
    handshake(&mut stream, &transfer_id).await?;

    let mut request = FrameHeader::new(FrameType::HasRequest, transfer_id, 0);
    request.object_id = Some(object_id.to_string());
    write_frame(&mut stream, &request, &[]).await?;

    let result = read_frame(&mut stream).await?;
    if result.header.frame_type != FrameType::HasResult {
        anyhow::bail!("expected has_result, got {:?}", result.header.frame_type);
    }
    Ok(HasOutcome {
        present: result.header.present.unwrap_or(false),
        object_id: result
            .header
            .object_id
            .unwrap_or_else(|| object_id.to_string()),
        reason: result.header.reason.unwrap_or_default(),
    })
}

pub async fn get_object(
    endpoint: &str,
    object_id: &str,
    chunk_size: usize,
) -> anyhow::Result<GetOutcome> {
    if chunk_size == 0 {
        anyhow::bail!("chunk_size must be greater than zero");
    }

    let transfer_id = new_transfer_id("get");
    let mut stream = TcpStream::connect(endpoint).await?;
    handshake(&mut stream, &transfer_id).await?;

    let mut request = FrameHeader::new(FrameType::GetBegin, transfer_id, 0);
    request.object_id = Some(object_id.to_string());
    request.chunk_size = Some(chunk_size as u64);
    write_frame(&mut stream, &request, &[]).await?;

    receive_get_response(&mut stream, object_id).await
}

pub async fn receive_get_response(
    stream: &mut TcpStream,
    requested_object_id: &str,
) -> anyhow::Result<GetOutcome> {
    let result = read_frame(stream).await?;
    if result.header.frame_type != FrameType::GetResult {
        anyhow::bail!("expected get_result, got {:?}", result.header.frame_type);
    }

    let object_id = result
        .header
        .object_id
        .clone()
        .unwrap_or_else(|| requested_object_id.to_string());
    let status = result.header.status.as_deref().unwrap_or("rejected");
    if status != "found" {
        return Ok(GetOutcome {
            found: false,
            object_id,
            reason: result.header.reason.unwrap_or_else(|| status.to_string()),
            metadata_bytes: Vec::new(),
            payload: Vec::new(),
        });
    }

    match receive_found_get(stream, result).await {
        Ok(mut outcome) => {
            if outcome.object_id.is_empty() {
                outcome.object_id = requested_object_id.to_string();
            }
            Ok(outcome)
        }
        Err(error) => Ok(GetOutcome {
            found: false,
            object_id,
            reason: error.to_string(),
            metadata_bytes: Vec::new(),
            payload: Vec::new(),
        }),
    }
}

async fn receive_found_get(stream: &mut TcpStream, result: Frame) -> anyhow::Result<GetOutcome> {
    let object_id = result.header.object_id.clone().unwrap_or_default();
    let metadata_bytes = result.payload;
    let descriptor_len = result
        .header
        .descriptor_len
        .ok_or_else(|| anyhow::anyhow!("get_result missing descriptor_len"))?;
    if descriptor_len != metadata_bytes.len() as u64 {
        anyhow::bail!("get_result descriptor_len mismatch");
    }

    let manifest = manifest_from_get_result(&result.header)?;
    let mut reassembler = Reassembler::new(manifest.clone())?;
    for _ in 0..manifest.total_chunks {
        let frame = read_frame(stream).await?;
        if frame.header.frame_type != FrameType::Chunk {
            anyhow::bail!("expected chunk, got {:?}", frame.header.frame_type);
        }
        let chunk_info = chunk_info_from_frame(&frame)?;
        reassembler.accept_chunk_info(&chunk_info, &frame.payload)?;
    }

    let done = read_frame(stream).await?;
    if done.header.frame_type != FrameType::GetResult {
        anyhow::bail!(
            "expected final get_result, got {:?}",
            done.header.frame_type
        );
    }
    if done.header.status.as_deref() != Some("success") {
        anyhow::bail!(
            "GET failed after chunks: {}",
            done.header.reason.unwrap_or_else(|| "unknown".to_string())
        );
    }

    let payload = reassembler.finish()?;
    let metadata: Value = serde_json::from_slice(&metadata_bytes)?;
    let validation = validate_object(&metadata, &payload, None);
    if validation.status != "accepted" {
        return Ok(GetOutcome {
            found: false,
            object_id,
            reason: validation.reason_code,
            metadata_bytes: Vec::new(),
            payload: Vec::new(),
        });
    }
    if validation.object_id.as_deref() != Some(object_id.as_str()) {
        return Ok(GetOutcome {
            found: false,
            object_id,
            reason: "object_id_mismatch".to_string(),
            metadata_bytes: Vec::new(),
            payload: Vec::new(),
        });
    }

    Ok(GetOutcome {
        found: true,
        object_id,
        reason: String::new(),
        metadata_bytes,
        payload,
    })
}

fn manifest_from_get_result(header: &FrameHeader) -> anyhow::Result<ChunkManifest> {
    let value = header
        .flags
        .as_ref()
        .and_then(|flags| flags.get("chunk_manifest"))
        .ok_or_else(|| anyhow::anyhow!("get_result missing chunk_manifest"))?;
    Ok(serde_json::from_value(value.clone())?)
}

fn chunk_info_from_frame(frame: &Frame) -> anyhow::Result<ChunkInfo> {
    let info = ChunkInfo {
        chunk_index: frame
            .header
            .chunk_index
            .ok_or_else(|| anyhow::anyhow!("chunk missing chunk_index"))?,
        offset: frame
            .header
            .chunk_offset
            .ok_or_else(|| anyhow::anyhow!("chunk missing chunk_offset"))?,
        len: frame
            .header
            .object_payload_len
            .ok_or_else(|| anyhow::anyhow!("chunk missing payload_len"))?,
        hash: frame
            .header
            .payload_hash
            .clone()
            .ok_or_else(|| anyhow::anyhow!("chunk missing chunk_hash"))?,
    };
    info.verify(&frame.payload)?;
    Ok(info)
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

async fn handshake(stream: &mut TcpStream, transfer_id: &str) -> anyhow::Result<()> {
    send_hello(stream, transfer_id).await?;
    let hello = read_frame(stream).await?;
    if hello.header.frame_type != FrameType::Hello {
        anyhow::bail!("expected daemon hello, got {:?}", hello.header.frame_type);
    }
    Ok(())
}

fn new_transfer_id(prefix: &str) -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);
    format!("{prefix}-{}-{nanos}", std::process::id())
}
