use crate::cache::validate_object;
use crate::transport::{
    chunk_bytes, iter_chunks, read_frame, write_frame, ChunkInfo, ChunkManifest, Frame,
    FrameHeader, FrameType, PathSpec, Reassembler, RoundRobinScheduler, TraceEvent, TraceSink,
    TransportMetrics, TransportResult, TRANSPORT_VERSION,
};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::time::{Instant, SystemTime, UNIX_EPOCH};
use tokio::net::TcpStream;

#[derive(Debug, Default, Clone)]
pub struct ClientTelemetry {
    pub metrics: TransportMetrics,
    pub trace: Option<TraceSink>,
}

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
    put_object_observed(
        endpoint,
        metadata_bytes,
        payload,
        chunk_size,
        target_profile,
        ClientTelemetry::default(),
    )
    .await
}

pub async fn put_object_observed(
    endpoint: &str,
    metadata_bytes: Vec<u8>,
    payload: Vec<u8>,
    chunk_size: usize,
    target_profile: Option<Value>,
    telemetry: ClientTelemetry,
) -> anyhow::Result<PutOutcome> {
    let metadata: Value = serde_json::from_slice(&metadata_bytes)?;
    let validation = validate_object(&metadata, &payload, target_profile.as_ref());
    if validation.status != "accepted" {
        telemetry.metrics.record_transfer_failed();
        emit_trace(
            &telemetry.trace,
            TraceEvent::new("transfer_error")
                .maybe_object_id(validation.object_id.as_deref())
                .reason_code(validation.reason_code.clone()),
        )?;
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
    put_validated_object_observed(endpoint, metadata_bytes, payload, manifest, telemetry).await
}

pub async fn put_object_multipath_observed(
    paths: Vec<PathSpec>,
    metadata_bytes: Vec<u8>,
    payload: Vec<u8>,
    chunk_size: usize,
    target_profile: Option<Value>,
    telemetry: ClientTelemetry,
) -> anyhow::Result<PutOutcome> {
    let metadata: Value = serde_json::from_slice(&metadata_bytes)?;
    let validation = validate_object(&metadata, &payload, target_profile.as_ref());
    if validation.status != "accepted" {
        telemetry.metrics.record_transfer_failed();
        emit_trace(
            &telemetry.trace,
            TraceEvent::new("transfer_error")
                .maybe_object_id(validation.object_id.as_deref())
                .reason_code(validation.reason_code.clone()),
        )?;
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
    manifest.object_id = Some(object_id);
    put_validated_object_multipath_observed(paths, metadata_bytes, payload, manifest, telemetry)
        .await
}

pub async fn put_validated_object(
    endpoint: &str,
    metadata_bytes: Vec<u8>,
    payload: Vec<u8>,
    manifest: ChunkManifest,
) -> anyhow::Result<PutOutcome> {
    put_validated_object_observed(
        endpoint,
        metadata_bytes,
        payload,
        manifest,
        ClientTelemetry::default(),
    )
    .await
}

pub async fn put_validated_object_observed(
    endpoint: &str,
    metadata_bytes: Vec<u8>,
    payload: Vec<u8>,
    manifest: ChunkManifest,
    telemetry: ClientTelemetry,
) -> anyhow::Result<PutOutcome> {
    let object_id = manifest
        .object_id
        .clone()
        .ok_or_else(|| anyhow::anyhow!("PUT manifest requires object_id"))?;
    let transfer_id = new_transfer_id("put");
    let mut stream = TcpStream::connect(endpoint).await?;

    handshake(&mut stream, &transfer_id).await?;
    telemetry.metrics.record_transfer_started();
    emit_trace(
        &telemetry.trace,
        TraceEvent::new("client_put_begin")
            .transfer_id(transfer_id.clone())
            .object_id(object_id.clone())
            .bytes(metadata_bytes.len() as u64),
    )?;

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
    telemetry
        .metrics
        .record_bytes_sent(metadata_bytes.len() as u64);

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
        let ack_started = Instant::now();
        write_frame(&mut stream, &header, chunk.bytes).await?;
        telemetry
            .metrics
            .record_chunk_sent(chunk.bytes.len() as u64);
        emit_trace(
            &telemetry.trace,
            TraceEvent::new("chunk_sent")
                .transfer_id(transfer_id.clone())
                .object_id(object_id.clone())
                .chunk_index(chunk.info.chunk_index)
                .bytes(chunk.bytes.len() as u64)
                .path_name("primary"),
        )?;

        let ack = read_frame(&mut stream).await?;
        let ack_duration_ms = ack_started.elapsed().as_millis() as u64;
        telemetry
            .metrics
            .record_chunk_ack_latency_ms(ack_duration_ms);
        if ack.header.frame_type != FrameType::ChunkAck {
            anyhow::bail!("expected chunk_ack, got {:?}", ack.header.frame_type);
        }
        let status = ack.header.status.as_deref().unwrap_or("rejected");
        emit_trace(
            &telemetry.trace,
            TraceEvent::new("chunk_ack")
                .transfer_id(transfer_id.clone())
                .object_id(object_id.clone())
                .chunk_index(chunk.info.chunk_index)
                .duration_ms(ack_duration_ms)
                .path_name("primary")
                .reason_code(ack.header.reason.clone().unwrap_or_default()),
        )?;
        if status != "accepted" && status != "duplicate" {
            let reason = ack
                .header
                .reason
                .unwrap_or_else(|| "chunk_rejected".to_string());
            telemetry.metrics.record_transfer_failed();
            emit_trace(
                &telemetry.trace,
                TraceEvent::new("transfer_error")
                    .transfer_id(transfer_id.clone())
                    .object_id(object_id.clone())
                    .chunk_index(chunk.info.chunk_index)
                    .reason_code(reason.clone()),
            )?;
            anyhow::bail!("chunk {} rejected: {}", chunk.info.chunk_index, reason);
        }
    }

    let mut commit = FrameHeader::new(FrameType::PutCommit, transfer_id.clone(), 0);
    commit.object_id = Some(object_id.clone());
    commit.total_chunks = Some(manifest.total_chunks);
    commit.object_payload_len = Some(manifest.payload_len);
    write_frame(&mut stream, &commit, &[]).await?;

    let result = read_frame(&mut stream).await?;
    if result.header.frame_type != FrameType::PutResult {
        anyhow::bail!("expected put_result, got {:?}", result.header.frame_type);
    }
    let status = result.header.status.as_deref().unwrap_or("rejected");
    if status == "committed" {
        telemetry.metrics.record_transfer_completed();
    } else {
        telemetry.metrics.record_transfer_failed();
    }
    Ok(PutOutcome {
        accepted: status == "committed",
        object_id,
        reason: result.header.reason.unwrap_or_default(),
    })
}

pub async fn put_validated_object_multipath_observed(
    paths: Vec<PathSpec>,
    metadata_bytes: Vec<u8>,
    payload: Vec<u8>,
    manifest: ChunkManifest,
    telemetry: ClientTelemetry,
) -> anyhow::Result<PutOutcome> {
    if paths.is_empty() {
        anyhow::bail!("multipath PUT requires at least one path");
    }

    let object_id = manifest
        .object_id
        .clone()
        .ok_or_else(|| anyhow::anyhow!("PUT manifest requires object_id"))?;
    let transfer_id = new_transfer_id("put");
    let mut scheduler = RoundRobinScheduler::new(paths);
    let mut connections = Vec::with_capacity(scheduler.paths().len());

    for index in 0..scheduler.paths().len() {
        let spec = scheduler.paths()[index].spec.clone();
        match TcpStream::connect(&spec.endpoint).await {
            Ok(mut stream) => match handshake(&mut stream, &transfer_id).await {
                Ok(()) => connections.push(Some(stream)),
                Err(error) => {
                    scheduler.mark_dead(index);
                    emit_trace(
                        &telemetry.trace,
                        TraceEvent::new("path_dead")
                            .transfer_id(transfer_id.clone())
                            .object_id(object_id.clone())
                            .path_name(spec.name)
                            .reason_code(error.to_string()),
                    )?;
                    connections.push(None);
                }
            },
            Err(error) => {
                scheduler.mark_dead(index);
                emit_trace(
                    &telemetry.trace,
                    TraceEvent::new("path_dead")
                        .transfer_id(transfer_id.clone())
                        .object_id(object_id.clone())
                        .path_name(spec.name)
                        .reason_code(error.to_string()),
                )?;
                connections.push(None);
            }
        }
    }

    if scheduler.healthy_path_count() == 0 {
        telemetry.metrics.record_transfer_failed();
        anyhow::bail!("all paths failed before PUT started");
    }

    telemetry.metrics.record_transfer_started();
    let begin_index = scheduler
        .select_path()
        .ok_or_else(|| anyhow::anyhow!("all paths failed before put_begin"))?;
    send_put_begin(
        connections[begin_index]
            .as_mut()
            .ok_or_else(|| anyhow::anyhow!("selected path has no connection"))?,
        &transfer_id,
        &object_id,
        &metadata_bytes,
        &manifest,
        true,
    )
    .await?;
    telemetry
        .metrics
        .record_bytes_sent(metadata_bytes.len() as u64);
    emit_trace(
        &telemetry.trace,
        TraceEvent::new("client_put_begin")
            .transfer_id(transfer_id.clone())
            .object_id(object_id.clone())
            .bytes(metadata_bytes.len() as u64)
            .path_name(scheduler.path_name(begin_index).to_string()),
    )?;

    for chunk in iter_chunks(&payload, manifest.chunk_size)? {
        let mut last_error = None;
        loop {
            let path_index = match scheduler.select_path() {
                Some(index) => index,
                None => {
                    telemetry.metrics.record_transfer_failed();
                    let reason = last_error.unwrap_or_else(|| "all paths failed".to_string());
                    emit_trace(
                        &telemetry.trace,
                        TraceEvent::new("transfer_error")
                            .transfer_id(transfer_id.clone())
                            .object_id(object_id.clone())
                            .chunk_index(chunk.info.chunk_index)
                            .reason_code(reason.clone()),
                    )?;
                    anyhow::bail!(reason);
                }
            };
            let path_name = scheduler.path_name(path_index).to_string();
            let Some(stream) = connections[path_index].as_mut() else {
                scheduler.mark_dead(path_index);
                continue;
            };

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
            header.flags = Some(BTreeMap::from([(
                "path_name".to_string(),
                json!(path_name.clone()),
            )]));

            scheduler.mark_send_started(path_index, chunk.bytes.len() as u64);
            let ack_started = Instant::now();
            if let Err(error) = write_frame(stream, &header, chunk.bytes).await {
                last_error = Some(error.to_string());
                connections[path_index] = None;
                scheduler.mark_dead(path_index);
                telemetry.metrics.record_chunk_retried();
                emit_trace(
                    &telemetry.trace,
                    TraceEvent::new("path_dead")
                        .transfer_id(transfer_id.clone())
                        .object_id(object_id.clone())
                        .path_name(path_name)
                        .reason_code(last_error.clone().unwrap_or_default()),
                )?;
                continue;
            }
            telemetry
                .metrics
                .record_chunk_sent(chunk.bytes.len() as u64);
            emit_trace(
                &telemetry.trace,
                TraceEvent::new("chunk_sent")
                    .transfer_id(transfer_id.clone())
                    .object_id(object_id.clone())
                    .chunk_index(chunk.info.chunk_index)
                    .bytes(chunk.bytes.len() as u64)
                    .path_name(path_name.clone()),
            )?;

            let ack = match read_frame(stream).await {
                Ok(ack) => ack,
                Err(error) => {
                    last_error = Some(error.to_string());
                    connections[path_index] = None;
                    scheduler.mark_dead(path_index);
                    telemetry.metrics.record_chunk_retried();
                    emit_trace(
                        &telemetry.trace,
                        TraceEvent::new("path_dead")
                            .transfer_id(transfer_id.clone())
                            .object_id(object_id.clone())
                            .chunk_index(chunk.info.chunk_index)
                            .path_name(path_name)
                            .reason_code(last_error.clone().unwrap_or_default()),
                    )?;
                    continue;
                }
            };

            let ack_duration = ack_started.elapsed();
            scheduler.mark_ack(path_index, ack_duration);
            let ack_duration_ms = ack_duration.as_millis() as u64;
            telemetry
                .metrics
                .record_chunk_ack_latency_ms(ack_duration_ms);
            if ack.header.frame_type != FrameType::ChunkAck {
                anyhow::bail!("expected chunk_ack, got {:?}", ack.header.frame_type);
            }
            let status = ack.header.status.as_deref().unwrap_or("rejected");
            emit_trace(
                &telemetry.trace,
                TraceEvent::new("chunk_ack")
                    .transfer_id(transfer_id.clone())
                    .object_id(object_id.clone())
                    .chunk_index(chunk.info.chunk_index)
                    .duration_ms(ack_duration_ms)
                    .path_name(path_name)
                    .reason_code(ack.header.reason.clone().unwrap_or_default()),
            )?;
            if status == "accepted" || status == "duplicate" {
                break;
            }

            let reason = ack
                .header
                .reason
                .unwrap_or_else(|| "chunk_rejected".to_string());
            telemetry.metrics.record_transfer_failed();
            emit_trace(
                &telemetry.trace,
                TraceEvent::new("transfer_error")
                    .transfer_id(transfer_id.clone())
                    .object_id(object_id.clone())
                    .chunk_index(chunk.info.chunk_index)
                    .reason_code(reason.clone()),
            )?;
            anyhow::bail!("chunk {} rejected: {}", chunk.info.chunk_index, reason);
        }
    }

    let commit_index = scheduler
        .select_path()
        .ok_or_else(|| anyhow::anyhow!("all paths failed before put_commit"))?;
    let commit_stream = connections[commit_index]
        .as_mut()
        .ok_or_else(|| anyhow::anyhow!("selected commit path has no connection"))?;
    let mut commit = FrameHeader::new(FrameType::PutCommit, transfer_id.clone(), 0);
    commit.object_id = Some(object_id.clone());
    commit.total_chunks = Some(manifest.total_chunks);
    commit.object_payload_len = Some(manifest.payload_len);
    commit.flags = Some(BTreeMap::from([(
        "path_name".to_string(),
        json!(scheduler.path_name(commit_index)),
    )]));
    write_frame(commit_stream, &commit, &[]).await?;

    let result = read_frame(commit_stream).await?;
    if result.header.frame_type != FrameType::PutResult {
        anyhow::bail!("expected put_result, got {:?}", result.header.frame_type);
    }
    let status = result.header.status.as_deref().unwrap_or("rejected");
    if status == "committed" {
        telemetry.metrics.record_transfer_completed();
    } else {
        telemetry.metrics.record_transfer_failed();
    }
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
    get_object_observed(endpoint, object_id, chunk_size, ClientTelemetry::default()).await
}

pub async fn get_object_observed(
    endpoint: &str,
    object_id: &str,
    chunk_size: usize,
    telemetry: ClientTelemetry,
) -> anyhow::Result<GetOutcome> {
    if chunk_size == 0 {
        anyhow::bail!("chunk_size must be greater than zero");
    }

    let transfer_id = new_transfer_id("get");
    let mut stream = TcpStream::connect(endpoint).await?;
    handshake(&mut stream, &transfer_id).await?;
    telemetry.metrics.record_transfer_started();
    emit_trace(
        &telemetry.trace,
        TraceEvent::new("get_begin")
            .transfer_id(transfer_id.clone())
            .object_id(object_id.to_string()),
    )?;

    let mut request = FrameHeader::new(FrameType::GetBegin, transfer_id, 0);
    request.object_id = Some(object_id.to_string());
    request.chunk_size = Some(chunk_size as u64);
    write_frame(&mut stream, &request, &[]).await?;

    let outcome = receive_get_response(&mut stream, object_id).await?;
    if outcome.found {
        telemetry.metrics.record_transfer_completed();
        telemetry
            .metrics
            .record_bytes_received(outcome.payload.len() as u64);
        emit_trace(
            &telemetry.trace,
            TraceEvent::new("get_completed")
                .transfer_id(request.transfer_id)
                .object_id(outcome.object_id.clone())
                .bytes(outcome.payload.len() as u64),
        )?;
    } else {
        telemetry.metrics.record_transfer_failed();
        emit_trace(
            &telemetry.trace,
            TraceEvent::new("transfer_error")
                .transfer_id(request.transfer_id)
                .object_id(outcome.object_id.clone())
                .reason_code(outcome.reason.clone()),
        )?;
    }
    Ok(outcome)
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

async fn send_put_begin(
    stream: &mut TcpStream,
    transfer_id: &str,
    object_id: &str,
    metadata_bytes: &[u8],
    manifest: &ChunkManifest,
    multipath: bool,
) -> anyhow::Result<()> {
    let mut begin = FrameHeader::new(
        FrameType::PutBegin,
        transfer_id.to_string(),
        metadata_bytes.len() as u64,
    );
    begin.object_id = Some(object_id.to_string());
    begin.total_chunks = Some(manifest.total_chunks);
    begin.descriptor_len = Some(metadata_bytes.len() as u64);
    begin.object_payload_len = Some(manifest.payload_len);
    begin.chunk_size = Some(manifest.chunk_size as u64);
    begin.payload_hash = Some(manifest.payload_hash.clone());
    begin.target_profile_id = Some("none".to_string());
    begin.flags = Some(BTreeMap::from([
        (
            "chunk_manifest".to_string(),
            serde_json::to_value(manifest)?,
        ),
        ("multipath".to_string(), json!(multipath)),
    ]));
    write_frame(stream, &begin, metadata_bytes).await?;
    Ok(())
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

fn emit_trace(trace: &Option<TraceSink>, event: TraceEvent) -> anyhow::Result<()> {
    if let Some(trace) = trace {
        trace.emit(event)?;
    }
    Ok(())
}
