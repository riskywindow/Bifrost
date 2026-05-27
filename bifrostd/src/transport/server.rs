use crate::spool::{CommitOutcome, Spool, SpoolError};
use crate::transport::{
    chunk_bytes, iter_chunks, read_frame, write_frame, ChunkManifest, Frame, FrameHeader,
    FrameType, TransportError, TransportResult, TRANSPORT_VERSION,
};
use std::collections::BTreeMap;
use std::net::SocketAddr;
use std::path::PathBuf;
use tokio::net::{TcpListener, TcpStream};

#[derive(Debug, Clone)]
pub struct ServerConfig {
    pub listen: String,
    pub spool_root: PathBuf,
}

pub async fn serve(config: ServerConfig) -> anyhow::Result<()> {
    let listener = TcpListener::bind(&config.listen).await?;
    serve_listener(listener, Spool::new(config.spool_root)).await
}

pub async fn serve_listener(listener: TcpListener, spool: Spool) -> anyhow::Result<()> {
    loop {
        let (stream, peer) = listener.accept().await?;
        let spool = spool.clone();
        tokio::spawn(async move {
            if let Err(error) = handle_connection(stream, spool, peer).await {
                eprintln!("bifrost-daemon: connection {peer} ended with error: {error}");
            }
        });
    }
}

pub async fn handle_connection(
    mut stream: TcpStream,
    spool: Spool,
    _peer: SocketAddr,
) -> anyhow::Result<()> {
    let hello = read_frame(&mut stream).await?;
    if hello.header.frame_type != FrameType::Hello {
        send_error(
            &mut stream,
            &hello.header.transfer_id,
            "expected hello as first frame",
        )
        .await?;
        return Ok(());
    }
    send_hello(&mut stream, &hello.header.transfer_id).await?;

    let mut active_transfer: Option<String> = None;
    loop {
        let frame = match read_frame(&mut stream).await {
            Ok(frame) => frame,
            Err(TransportError::Io(err))
                if err.kind() == std::io::ErrorKind::UnexpectedEof
                    || err.kind() == std::io::ErrorKind::ConnectionReset =>
            {
                if let Some(transfer_id) = active_transfer.take() {
                    let _ = spool.abort_staging_transfer(&transfer_id);
                }
                return Ok(());
            }
            Err(error) => return Err(error.into()),
        };

        match frame.header.frame_type {
            FrameType::PutBegin => {
                let transfer_id = frame.header.transfer_id.clone();
                let result = begin_put(&spool, &frame);
                match result {
                    Ok(()) => active_transfer = Some(transfer_id),
                    Err(error) => {
                        send_error(&mut stream, &frame.header.transfer_id, &error.to_string())
                            .await?;
                        return Ok(());
                    }
                }
            }
            FrameType::Chunk => {
                let status = match write_chunk(&spool, &frame) {
                    Ok(()) => ("accepted", ""),
                    Err(error) => {
                        send_error(&mut stream, &frame.header.transfer_id, &error.to_string())
                            .await?;
                        let _ = spool.abort_staging_transfer(&frame.header.transfer_id);
                        return Ok(());
                    }
                };
                send_chunk_ack(&mut stream, &frame, status.0, status.1).await?;
            }
            FrameType::PutCommit => {
                send_put_result(&mut stream, &spool, &frame).await?;
                return Ok(());
            }
            FrameType::HasRequest => {
                send_has_result(&mut stream, &spool, &frame).await?;
                return Ok(());
            }
            FrameType::GetBegin => {
                send_get_response(&mut stream, &spool, &frame).await?;
                return Ok(());
            }
            _ => {
                send_error(
                    &mut stream,
                    &frame.header.transfer_id,
                    &format!("unexpected frame type {:?}", frame.header.frame_type),
                )
                .await?;
                return Ok(());
            }
        }
    }
}

fn begin_put(spool: &Spool, frame: &Frame) -> anyhow::Result<()> {
    let manifest = manifest_from_begin(frame)?;
    let object_id = frame
        .header
        .object_id
        .as_deref()
        .ok_or_else(|| anyhow::anyhow!("put_begin missing object_id"))?;
    if manifest.object_id.as_deref() != Some(object_id) {
        anyhow::bail!("put_begin manifest object_id mismatch");
    }
    if frame.header.descriptor_len != Some(frame.payload.len() as u64) {
        anyhow::bail!("put_begin descriptor_len mismatch");
    }
    if frame.header.total_chunks != Some(manifest.total_chunks) {
        anyhow::bail!("put_begin chunk_count mismatch");
    }
    if frame.header.object_payload_len != Some(manifest.payload_len) {
        anyhow::bail!("put_begin payload_len mismatch");
    }
    spool.create_staging_transfer(&frame.header.transfer_id, &frame.payload, &manifest)?;
    Ok(())
}

fn manifest_from_begin(frame: &Frame) -> anyhow::Result<ChunkManifest> {
    let value = frame
        .header
        .flags
        .as_ref()
        .and_then(|flags| flags.get("chunk_manifest"))
        .ok_or_else(|| anyhow::anyhow!("put_begin missing chunk_manifest"))?;
    Ok(serde_json::from_value(value.clone())?)
}

fn write_chunk(spool: &Spool, frame: &Frame) -> anyhow::Result<()> {
    let chunk_index = frame
        .header
        .chunk_index
        .ok_or_else(|| anyhow::anyhow!("chunk missing chunk_index"))?;
    spool.write_chunk(&frame.header.transfer_id, chunk_index, &frame.payload)?;
    Ok(())
}

async fn send_hello(stream: &mut TcpStream, transfer_id: &str) -> TransportResult<()> {
    let mut hello = FrameHeader::new(FrameType::Hello, transfer_id, 0);
    hello.peer_role = Some("daemon".to_string());
    hello.supported_versions = Some(vec![TRANSPORT_VERSION.to_string()]);
    write_frame(stream, &hello, &[]).await
}

async fn send_chunk_ack(
    stream: &mut TcpStream,
    request: &Frame,
    status: &str,
    reason: &str,
) -> TransportResult<()> {
    let mut ack = FrameHeader::new(FrameType::ChunkAck, request.header.transfer_id.clone(), 0);
    ack.object_id = request.header.object_id.clone();
    ack.chunk_index = request.header.chunk_index;
    ack.status = Some(status.to_string());
    ack.reason = Some(reason.to_string());
    write_frame(stream, &ack, &[]).await
}

async fn send_put_result(
    stream: &mut TcpStream,
    spool: &Spool,
    request: &Frame,
) -> TransportResult<()> {
    let object_id = request.header.object_id.clone().unwrap_or_default();
    let (status, reason) = match spool.commit_transfer(&request.header.transfer_id, None) {
        Ok(CommitOutcome::Committed { .. }) | Ok(CommitOutcome::AlreadyCommitted { .. }) => {
            ("committed", String::new())
        }
        Err(SpoolError::ValidationRejected(reason)) => ("rejected", reason),
        Err(error) => ("rejected", error.to_string()),
    };

    let mut result = FrameHeader::new(FrameType::PutResult, request.header.transfer_id.clone(), 0);
    result.object_id = Some(object_id);
    result.status = Some(status.to_string());
    result.reason = Some(reason);
    write_frame(stream, &result, &[]).await
}

async fn send_has_result(
    stream: &mut TcpStream,
    spool: &Spool,
    request: &Frame,
) -> TransportResult<()> {
    let object_id = request.header.object_id.clone().unwrap_or_default();
    let present = spool.has_object(&object_id);
    let mut result = FrameHeader::new(FrameType::HasResult, request.header.transfer_id.clone(), 0);
    result.object_id = Some(object_id);
    result.present = Some(present);
    result.reason = Some(if present {
        String::new()
    } else {
        "not_found".to_string()
    });
    write_frame(stream, &result, &[]).await
}

async fn send_get_response(
    stream: &mut TcpStream,
    spool: &Spool,
    request: &Frame,
) -> TransportResult<()> {
    let object_id = request.header.object_id.clone().unwrap_or_default();
    let metadata = match spool.read_metadata(&object_id) {
        Ok(metadata) => metadata,
        Err(SpoolError::NotFound(_)) => {
            send_get_result_miss(stream, request, &object_id, "not_found").await?;
            return Ok(());
        }
        Err(error) => {
            send_get_result_miss(stream, request, &object_id, &error.to_string()).await?;
            return Ok(());
        }
    };
    let payload = match spool.read_payload(&object_id) {
        Ok(payload) => payload,
        Err(SpoolError::NotFound(_)) => {
            send_get_result_miss(stream, request, &object_id, "not_found").await?;
            return Ok(());
        }
        Err(error) => {
            send_get_result_miss(stream, request, &object_id, &error.to_string()).await?;
            return Ok(());
        }
    };

    let chunk_size = request
        .header
        .chunk_size
        .map(|value| value as usize)
        .unwrap_or(crate::transport::DEFAULT_CHUNK_SIZE);
    let mut manifest = match chunk_bytes(&payload, chunk_size) {
        Ok(manifest) => manifest,
        Err(error) => {
            send_get_result_miss(stream, request, &object_id, &error.to_string()).await?;
            return Ok(());
        }
    };
    manifest.object_id = Some(object_id.clone());

    let mut result = FrameHeader::new(
        FrameType::GetResult,
        request.header.transfer_id.clone(),
        metadata.len() as u64,
    );
    result.object_id = Some(object_id.clone());
    result.status = Some("found".to_string());
    result.reason = Some(String::new());
    result.descriptor_len = Some(metadata.len() as u64);
    result.object_payload_len = Some(manifest.payload_len);
    result.chunk_size = Some(manifest.chunk_size as u64);
    result.total_chunks = Some(manifest.total_chunks);
    result.payload_hash = Some(manifest.payload_hash.clone());
    result.flags = Some(BTreeMap::from([(
        "chunk_manifest".to_string(),
        serde_json::to_value(&manifest)?,
    )]));
    write_frame(stream, &result, &metadata).await?;

    for chunk in iter_chunks(&payload, manifest.chunk_size)? {
        let mut header = FrameHeader::new(
            FrameType::Chunk,
            request.header.transfer_id.clone(),
            chunk.bytes.len() as u64,
        );
        header.object_id = Some(object_id.clone());
        header.chunk_index = Some(chunk.info.chunk_index);
        header.total_chunks = Some(manifest.total_chunks);
        header.chunk_offset = Some(chunk.info.offset);
        header.object_payload_len = Some(chunk.info.len);
        header.payload_hash = Some(chunk.info.hash.clone());
        write_frame(stream, &header, chunk.bytes).await?;
    }

    let mut done = FrameHeader::new(FrameType::GetResult, request.header.transfer_id.clone(), 0);
    done.object_id = Some(object_id);
    done.status = Some("success".to_string());
    done.reason = Some(String::new());
    done.descriptor_len = Some(metadata.len() as u64);
    done.object_payload_len = Some(manifest.payload_len);
    done.chunk_size = Some(manifest.chunk_size as u64);
    done.total_chunks = Some(manifest.total_chunks);
    done.payload_hash = Some(manifest.payload_hash);
    write_frame(stream, &done, &[]).await
}

async fn send_get_result_miss(
    stream: &mut TcpStream,
    request: &Frame,
    object_id: &str,
    reason: &str,
) -> TransportResult<()> {
    let mut result = FrameHeader::new(FrameType::GetResult, request.header.transfer_id.clone(), 0);
    result.object_id = Some(object_id.to_string());
    result.status = Some("miss".to_string());
    result.reason = Some(reason.to_string());
    result.descriptor_len = Some(0);
    result.object_payload_len = Some(0);
    result.chunk_size = Some(0);
    result.total_chunks = Some(0);
    write_frame(stream, &result, &[]).await
}

async fn send_error(
    stream: &mut TcpStream,
    transfer_id: &str,
    reason: &str,
) -> TransportResult<()> {
    let mut error = FrameHeader::new(FrameType::Error, transfer_id, 0);
    error.status = Some("rejected".to_string());
    error.reason = Some(reason.to_string());
    error.flags = Some(BTreeMap::new());
    write_frame(stream, &error, &[]).await
}
