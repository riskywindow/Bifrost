use crate::cache::validate_object;
use crate::transport::{
    chunk_bytes, iter_chunks, read_frame, write_frame, ChunkInfo, ChunkManifest, Frame,
    FrameHeader, FrameType, OpaqueKeyListRequest, OpaqueKeyListResponse, OpaqueKeyQueryRequest,
    OpaqueKeyQueryResponse, OpaqueKeySummary, PathSpec, PathStatus, Reassembler,
    RoundRobinScheduler, StoreEvictRequest, StoreEvictResponse, StoreInspectResponse,
    StoreListResponse, StoreManifestRequest, StoreManifestResponse, StoreObjectFilter,
    StoreObjectSummary, StoreOperationResponse, StoreStatsResponse, StoreTtlRequest, TraceEvent,
    TraceSink, TransportMetrics, TransportResult, TRANSPORT_VERSION,
};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::net::TcpStream;
use tokio::time::timeout;

pub const DEFAULT_CHUNK_TIMEOUT_MS: u64 = 5_000;
pub const DEFAULT_MAX_RETRIES_PER_CHUNK: u32 = 3;
pub const DEFAULT_MAX_INFLIGHT_PER_PATH: u64 = 16;

#[derive(Debug, Default, Clone)]
pub struct ClientTelemetry {
    pub metrics: TransportMetrics,
    pub trace: Option<TraceSink>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MultipathPutOptions {
    pub chunk_timeout: Duration,
    pub max_retries_per_chunk: u32,
    pub max_inflight_per_path: u64,
}

impl Default for MultipathPutOptions {
    fn default() -> Self {
        Self {
            chunk_timeout: Duration::from_millis(DEFAULT_CHUNK_TIMEOUT_MS),
            max_retries_per_chunk: DEFAULT_MAX_RETRIES_PER_CHUNK,
            max_inflight_per_path: DEFAULT_MAX_INFLIGHT_PER_PATH,
        }
    }
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

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoreListOutcome {
    pub objects: Vec<StoreObjectSummary>,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OpaqueKeyQueryOutcome {
    pub found: bool,
    pub key: Option<OpaqueKeySummary>,
    pub object: Option<StoreObjectSummary>,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OpaqueKeyListOutcome {
    pub keys: Vec<OpaqueKeySummary>,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoreInspectOutcome {
    pub found: bool,
    pub response: StoreInspectResponse,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoreStatsOutcome {
    pub stats: StoreStatsResponse,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoreEvictOutcome {
    pub report: crate::store::EvictionReport,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoreFsckOutcome {
    pub result: crate::store::FsckResult,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoreManifestOutcome {
    pub response: StoreManifestResponse,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoreOperationOutcome {
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
    put_object_multipath_observed_with_options(
        paths,
        metadata_bytes,
        payload,
        chunk_size,
        target_profile,
        telemetry,
        MultipathPutOptions::default(),
    )
    .await
}

pub async fn put_object_multipath_observed_with_options(
    paths: Vec<PathSpec>,
    metadata_bytes: Vec<u8>,
    payload: Vec<u8>,
    chunk_size: usize,
    target_profile: Option<Value>,
    telemetry: ClientTelemetry,
    options: MultipathPutOptions,
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
    put_validated_object_multipath_observed_with_options(
        paths,
        metadata_bytes,
        payload,
        manifest,
        telemetry,
        options,
    )
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
    put_validated_object_multipath_observed_with_options(
        paths,
        metadata_bytes,
        payload,
        manifest,
        telemetry,
        MultipathPutOptions::default(),
    )
    .await
}

pub async fn put_validated_object_multipath_observed_with_options(
    paths: Vec<PathSpec>,
    metadata_bytes: Vec<u8>,
    payload: Vec<u8>,
    manifest: ChunkManifest,
    telemetry: ClientTelemetry,
    options: MultipathPutOptions,
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
                    telemetry.metrics.record_path_dead();
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
                telemetry.metrics.record_path_dead();
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
        emit_trace(
            &telemetry.trace,
            TraceEvent::new("transfer_failed")
                .transfer_id(transfer_id.clone())
                .object_id(object_id.clone())
                .reason_code("all paths failed before PUT started"),
        )?;
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
    synchronize_multipath_begin(
        connections[begin_index]
            .as_mut()
            .ok_or_else(|| anyhow::anyhow!("selected path has no connection"))?,
        &transfer_id,
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
        let mut retry_count = 0u32;
        let mut last_path_index = None;
        loop {
            let path_index = match scheduler
                .select_path_avoiding(last_path_index, options.max_inflight_per_path)
            {
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
                    emit_trace(
                        &telemetry.trace,
                        TraceEvent::new("transfer_failed")
                            .transfer_id(transfer_id.clone())
                            .object_id(object_id.clone())
                            .chunk_index(chunk.info.chunk_index)
                            .reason_code(reason.clone()),
                    )?;
                    anyhow::bail!(reason);
                }
            };
            let path_name = scheduler.path_name(path_index).to_string();
            if connections[path_index].is_none() {
                let endpoint = scheduler.paths()[path_index].spec.endpoint.clone();
                match TcpStream::connect(&endpoint).await {
                    Ok(mut stream) => match handshake(&mut stream, &transfer_id).await {
                        Ok(()) => {
                            connections[path_index] = Some(stream);
                        }
                        Err(error) => {
                            last_error = Some(error.to_string());
                            scheduler.mark_dead(path_index);
                            telemetry.metrics.record_path_dead();
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
                    },
                    Err(error) => {
                        last_error = Some(error.to_string());
                        scheduler.mark_dead(path_index);
                        telemetry.metrics.record_path_dead();
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
                }
            }
            let stream = connections[path_index]
                .as_mut()
                .ok_or_else(|| anyhow::anyhow!("selected path has no connection"))?;

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
                let status = scheduler.mark_failure(path_index);
                retry_count += 1;
                if retry_count > options.max_retries_per_chunk {
                    telemetry.metrics.record_transfer_failed();
                    emit_transfer_failed(
                        &telemetry.trace,
                        &transfer_id,
                        &object_id,
                        chunk.info.chunk_index,
                        "max_retries_exceeded",
                    )?;
                    anyhow::bail!(
                        "chunk {} exceeded max retries after send failure: {}",
                        chunk.info.chunk_index,
                        last_error.clone().unwrap_or_default()
                    );
                }
                telemetry.metrics.record_chunk_retried();
                emit_retry_trace(
                    &telemetry.trace,
                    &transfer_id,
                    &object_id,
                    chunk.info.chunk_index,
                    &path_name,
                    retry_count,
                    last_error.clone().unwrap_or_default(),
                )?;
                emit_path_status_trace(
                    &telemetry,
                    &transfer_id,
                    &object_id,
                    chunk.info.chunk_index,
                    &path_name,
                    status,
                    last_error.clone().unwrap_or_default(),
                )?;
                last_path_index = Some(path_index);
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

            let ack = match timeout(options.chunk_timeout, read_frame(stream)).await {
                Ok(Ok(ack)) => ack,
                Err(_) => {
                    last_error = Some("chunk_ack_timeout".to_string());
                    connections[path_index] = None;
                    let status = scheduler.mark_timeout(path_index);
                    telemetry.metrics.record_chunk_timeout();
                    retry_count += 1;
                    emit_trace(
                        &telemetry.trace,
                        TraceEvent::new("chunk_timeout")
                            .transfer_id(transfer_id.clone())
                            .object_id(object_id.clone())
                            .chunk_index(chunk.info.chunk_index)
                            .duration_ms(options.chunk_timeout.as_millis() as u64)
                            .path_name(path_name.clone())
                            .reason_code("chunk_ack_timeout"),
                    )?;
                    if retry_count > options.max_retries_per_chunk {
                        telemetry.metrics.record_transfer_failed();
                        emit_transfer_failed(
                            &telemetry.trace,
                            &transfer_id,
                            &object_id,
                            chunk.info.chunk_index,
                            "max_retries_exceeded",
                        )?;
                        anyhow::bail!(
                            "chunk {} exceeded max retries after ack timeout",
                            chunk.info.chunk_index
                        );
                    }
                    telemetry.metrics.record_chunk_retried();
                    emit_retry_trace(
                        &telemetry.trace,
                        &transfer_id,
                        &object_id,
                        chunk.info.chunk_index,
                        &path_name,
                        retry_count,
                        "chunk_ack_timeout",
                    )?;
                    emit_path_status_trace(
                        &telemetry,
                        &transfer_id,
                        &object_id,
                        chunk.info.chunk_index,
                        &path_name,
                        status,
                        "chunk_ack_timeout",
                    )?;
                    last_path_index = Some(path_index);
                    continue;
                }
                Ok(Err(error)) => {
                    last_error = Some(error.to_string());
                    connections[path_index] = None;
                    let status = scheduler.mark_failure(path_index);
                    retry_count += 1;
                    if retry_count > options.max_retries_per_chunk {
                        telemetry.metrics.record_transfer_failed();
                        emit_transfer_failed(
                            &telemetry.trace,
                            &transfer_id,
                            &object_id,
                            chunk.info.chunk_index,
                            "max_retries_exceeded",
                        )?;
                        anyhow::bail!(
                            "chunk {} exceeded max retries after ack failure: {}",
                            chunk.info.chunk_index,
                            last_error.clone().unwrap_or_default()
                        );
                    }
                    telemetry.metrics.record_chunk_retried();
                    emit_retry_trace(
                        &telemetry.trace,
                        &transfer_id,
                        &object_id,
                        chunk.info.chunk_index,
                        &path_name,
                        retry_count,
                        last_error.clone().unwrap_or_default(),
                    )?;
                    emit_path_status_trace(
                        &telemetry,
                        &transfer_id,
                        &object_id,
                        chunk.info.chunk_index,
                        &path_name,
                        status,
                        last_error.clone().unwrap_or_default(),
                    )?;
                    last_path_index = Some(path_index);
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
        emit_trace(
            &telemetry.trace,
            TraceEvent::new("transfer_completed")
                .transfer_id(transfer_id.clone())
                .object_id(object_id.clone())
                .path_name(scheduler.path_name(commit_index).to_string()),
        )?;
    } else {
        telemetry.metrics.record_transfer_failed();
        emit_transfer_failed(
            &telemetry.trace,
            &transfer_id,
            &object_id,
            0,
            result.header.reason.clone().unwrap_or_default(),
        )?;
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

pub async fn list_store_objects(
    endpoint: &str,
    filter: StoreObjectFilter,
) -> anyhow::Result<StoreListOutcome> {
    let payload = serde_json::to_vec(&filter)?;
    let result = store_json_request(
        endpoint,
        FrameType::ListRequest,
        FrameType::ListResult,
        None,
        payload,
    )
    .await?;
    if result.header.status.as_deref() != Some("ok") {
        return Ok(StoreListOutcome {
            objects: Vec::new(),
            reason: result
                .header
                .reason
                .unwrap_or_else(|| "store_list_failed".to_string()),
        });
    }
    let response: StoreListResponse = serde_json::from_slice(&result.payload)?;
    Ok(StoreListOutcome {
        objects: response.objects,
        reason: result.header.reason.unwrap_or_default(),
    })
}

pub async fn query_store_objects(
    endpoint: &str,
    filter: StoreObjectFilter,
) -> anyhow::Result<StoreListOutcome> {
    let payload = serde_json::to_vec(&filter)?;
    let result = store_json_request(
        endpoint,
        FrameType::QueryRequest,
        FrameType::QueryResult,
        None,
        payload,
    )
    .await?;
    if result.header.status.as_deref() != Some("ok") {
        return Ok(StoreListOutcome {
            objects: Vec::new(),
            reason: result
                .header
                .reason
                .unwrap_or_else(|| "store_query_failed".to_string()),
        });
    }
    let response: StoreListResponse = serde_json::from_slice(&result.payload)?;
    Ok(StoreListOutcome {
        objects: response.objects,
        reason: result.header.reason.unwrap_or_default(),
    })
}

pub async fn query_opaque_key(
    endpoint: &str,
    request: OpaqueKeyQueryRequest,
) -> anyhow::Result<OpaqueKeyQueryOutcome> {
    let payload = serde_json::to_vec(&request)?;
    let result = store_json_request(
        endpoint,
        FrameType::OpaqueKeyQueryRequest,
        FrameType::OpaqueKeyQueryResult,
        None,
        payload,
    )
    .await?;
    if result.header.status.as_deref() != Some("ok") {
        return Ok(OpaqueKeyQueryOutcome {
            found: false,
            key: None,
            object: None,
            reason: result
                .header
                .reason
                .unwrap_or_else(|| "opaque_key_query_failed".to_string()),
        });
    }
    let response: OpaqueKeyQueryResponse = serde_json::from_slice(&result.payload)?;
    Ok(OpaqueKeyQueryOutcome {
        found: response.found,
        key: response.key,
        object: response.object,
        reason: response.reason.unwrap_or_default(),
    })
}

pub async fn list_opaque_keys(
    endpoint: &str,
    request: OpaqueKeyListRequest,
) -> anyhow::Result<OpaqueKeyListOutcome> {
    let payload = serde_json::to_vec(&request)?;
    let result = store_json_request(
        endpoint,
        FrameType::OpaqueKeyListRequest,
        FrameType::OpaqueKeyListResult,
        None,
        payload,
    )
    .await?;
    if result.header.status.as_deref() != Some("ok") {
        return Ok(OpaqueKeyListOutcome {
            keys: Vec::new(),
            reason: result
                .header
                .reason
                .unwrap_or_else(|| "opaque_key_list_failed".to_string()),
        });
    }
    let response: OpaqueKeyListResponse = serde_json::from_slice(&result.payload)?;
    Ok(OpaqueKeyListOutcome {
        keys: response.keys,
        reason: result.header.reason.unwrap_or_default(),
    })
}

pub async fn inspect_store_object(
    endpoint: &str,
    object_id: &str,
) -> anyhow::Result<StoreInspectOutcome> {
    let result = store_json_request(
        endpoint,
        FrameType::InspectRequest,
        FrameType::InspectResult,
        Some(object_id.to_string()),
        Vec::new(),
    )
    .await?;
    let status = result.header.status.as_deref().unwrap_or("error");
    if status == "error" {
        return Ok(StoreInspectOutcome {
            found: false,
            response: StoreInspectResponse::miss(
                result
                    .header
                    .reason
                    .clone()
                    .unwrap_or_else(|| "store_inspect_failed".to_string()),
            ),
            reason: result.header.reason.unwrap_or_default(),
        });
    }
    let response: StoreInspectResponse = serde_json::from_slice(&result.payload)?;
    Ok(StoreInspectOutcome {
        found: status == "ok" && response.found,
        reason: result.header.reason.unwrap_or_default(),
        response,
    })
}

pub async fn store_stats(endpoint: &str) -> anyhow::Result<StoreStatsOutcome> {
    let result = store_json_request(
        endpoint,
        FrameType::StatsRequest,
        FrameType::StatsResult,
        None,
        Vec::new(),
    )
    .await?;
    if result.header.status.as_deref() != Some("ok") {
        return Ok(StoreStatsOutcome {
            stats: StoreStatsResponse::default(),
            reason: result
                .header
                .reason
                .unwrap_or_else(|| "store_stats_failed".to_string()),
        });
    }
    Ok(StoreStatsOutcome {
        stats: serde_json::from_slice(&result.payload)?,
        reason: result.header.reason.unwrap_or_default(),
    })
}

pub async fn evict_store(
    endpoint: &str,
    request: StoreEvictRequest,
) -> anyhow::Result<StoreEvictOutcome> {
    let payload = serde_json::to_vec(&request)?;
    let result = store_json_request(
        endpoint,
        FrameType::EvictRequest,
        FrameType::EvictResult,
        None,
        payload,
    )
    .await?;
    if result.header.status.as_deref() != Some("ok") {
        return Ok(StoreEvictOutcome {
            report: crate::store::EvictionReport::empty(
                &crate::store::EvictionRequest {
                    policy: request
                        .policy
                        .parse()
                        .unwrap_or(crate::store::EvictionPolicy::Lru),
                    target_bytes: request.target_bytes,
                    max_objects: request.max_objects,
                    dry_run: request.dry_run,
                    now_unix_ms: request.now_unix_ms.unwrap_or_default(),
                },
                0,
            ),
            reason: result
                .header
                .reason
                .unwrap_or_else(|| "store_evict_failed".to_string()),
        });
    }
    let response: StoreEvictResponse = serde_json::from_slice(&result.payload)?;
    Ok(StoreEvictOutcome {
        report: response.report,
        reason: result.header.reason.unwrap_or_default(),
    })
}

pub async fn fsck_store(
    endpoint: &str,
    mode: crate::store::FsckMode,
) -> anyhow::Result<StoreFsckOutcome> {
    let payload = serde_json::to_vec(&crate::transport::StoreFsckRequest { mode })?;
    let result = store_json_request(
        endpoint,
        FrameType::FsckRequest,
        FrameType::FsckResult,
        None,
        payload,
    )
    .await?;
    if result.header.status.as_deref() != Some("ok") {
        return Ok(StoreFsckOutcome {
            result: crate::store::FsckResult {
                status: crate::store::FsckStatus::Dirty,
                findings: Vec::new(),
                counts_by_severity: Default::default(),
                mutations_applied: Vec::new(),
                warnings: Vec::new(),
            },
            reason: result
                .header
                .reason
                .unwrap_or_else(|| "store_fsck_failed".to_string()),
        });
    }
    let response: crate::transport::StoreFsckResponse = serde_json::from_slice(&result.payload)?;
    Ok(StoreFsckOutcome {
        result: response.result,
        reason: result.header.reason.unwrap_or_default(),
    })
}

pub async fn pin_object(endpoint: &str, object_id: &str) -> anyhow::Result<StoreOperationOutcome> {
    store_operation_request(
        endpoint,
        FrameType::PinRequest,
        FrameType::PinResult,
        object_id,
        Vec::new(),
    )
    .await
}

pub async fn unpin_object(
    endpoint: &str,
    object_id: &str,
) -> anyhow::Result<StoreOperationOutcome> {
    store_operation_request(
        endpoint,
        FrameType::UnpinRequest,
        FrameType::UnpinResult,
        object_id,
        Vec::new(),
    )
    .await
}

pub async fn set_ttl(
    endpoint: &str,
    object_id: &str,
    expires_at_unix_ms: i64,
) -> anyhow::Result<StoreOperationOutcome> {
    store_operation_request(
        endpoint,
        FrameType::TtlRequest,
        FrameType::TtlResult,
        object_id,
        serde_json::to_vec(&StoreTtlRequest::Set { expires_at_unix_ms })?,
    )
    .await
}

pub async fn clear_ttl(endpoint: &str, object_id: &str) -> anyhow::Result<StoreOperationOutcome> {
    store_operation_request(
        endpoint,
        FrameType::TtlRequest,
        FrameType::TtlResult,
        object_id,
        serde_json::to_vec(&StoreTtlRequest::Clear)?,
    )
    .await
}

pub async fn quarantine_object(
    endpoint: &str,
    object_id: &str,
    reason: &str,
) -> anyhow::Result<StoreOperationOutcome> {
    store_operation_request(
        endpoint,
        FrameType::LifecycleRequest,
        FrameType::LifecycleResult,
        object_id,
        serde_json::to_vec(&crate::transport::StoreLifecycleRequest::Quarantine {
            reason: reason.to_string(),
        })?,
    )
    .await
}

pub async fn create_prefix_manifest(
    endpoint: &str,
    model_hash: Option<String>,
    tokenizer_hash: Option<String>,
    rope_config_hash: Option<String>,
    prefix_hash: String,
    token_range_start: i64,
    token_range_end: i64,
) -> anyhow::Result<StoreManifestOutcome> {
    manifest_request(
        endpoint,
        StoreManifestRequest::CreatePrefix {
            model_hash,
            tokenizer_hash,
            rope_config_hash,
            prefix_hash,
            token_range_start,
            token_range_end,
        },
    )
    .await
}

pub async fn manifest_add_member(
    endpoint: &str,
    manifest_id: &str,
    object_id: &str,
    required: bool,
) -> anyhow::Result<StoreManifestOutcome> {
    manifest_request(
        endpoint,
        StoreManifestRequest::AddMember {
            manifest_id: manifest_id.to_string(),
            object_id: object_id.to_string(),
            required,
        },
    )
    .await
}

pub async fn inspect_manifest(
    endpoint: &str,
    manifest_id: &str,
) -> anyhow::Result<StoreManifestOutcome> {
    manifest_request(
        endpoint,
        StoreManifestRequest::Inspect {
            manifest_id: manifest_id.to_string(),
        },
    )
    .await
}

pub async fn list_manifests(
    endpoint: &str,
    filter: crate::store::ManifestListFilter,
) -> anyhow::Result<StoreManifestOutcome> {
    manifest_request(endpoint, StoreManifestRequest::List { filter }).await
}

pub async fn check_manifest(
    endpoint: &str,
    manifest_id: &str,
) -> anyhow::Result<StoreManifestOutcome> {
    manifest_request(
        endpoint,
        StoreManifestRequest::Check {
            manifest_id: manifest_id.to_string(),
        },
    )
    .await
}

pub async fn manifest_pin(
    endpoint: &str,
    manifest_id: &str,
) -> anyhow::Result<StoreManifestOutcome> {
    manifest_request(
        endpoint,
        StoreManifestRequest::Pin {
            manifest_id: manifest_id.to_string(),
        },
    )
    .await
}

pub async fn manifest_unpin(
    endpoint: &str,
    manifest_id: &str,
) -> anyhow::Result<StoreManifestOutcome> {
    manifest_request(
        endpoint,
        StoreManifestRequest::Unpin {
            manifest_id: manifest_id.to_string(),
        },
    )
    .await
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

async fn store_json_request(
    endpoint: &str,
    request_type: FrameType,
    response_type: FrameType,
    object_id: Option<String>,
    payload: Vec<u8>,
) -> anyhow::Result<Frame> {
    let transfer_id = new_transfer_id("store");
    let mut stream = TcpStream::connect(endpoint).await?;
    handshake(&mut stream, &transfer_id).await?;

    let mut request = FrameHeader::new(request_type, transfer_id, payload.len() as u64);
    request.object_id = object_id;
    write_frame(&mut stream, &request, &payload).await?;

    let result = read_frame(&mut stream).await?;
    if result.header.frame_type != response_type {
        anyhow::bail!(
            "expected {:?}, got {:?}",
            response_type,
            result.header.frame_type
        );
    }
    Ok(result)
}

async fn manifest_request(
    endpoint: &str,
    request: StoreManifestRequest,
) -> anyhow::Result<StoreManifestOutcome> {
    let payload = serde_json::to_vec(&request)?;
    let result = store_json_request(
        endpoint,
        FrameType::ManifestRequest,
        FrameType::ManifestResult,
        None,
        payload,
    )
    .await?;
    if result.header.status.as_deref() != Some("ok") {
        return Ok(StoreManifestOutcome {
            response: StoreManifestResponse {
                status: "error".to_string(),
                reason: result.header.reason.clone().unwrap_or_default(),
                manifest: None,
                manifests: Vec::new(),
                completeness: None,
            },
            reason: result.header.reason.unwrap_or_default(),
        });
    }
    let response: StoreManifestResponse = serde_json::from_slice(&result.payload)?;
    Ok(StoreManifestOutcome {
        reason: response.reason.clone(),
        response,
    })
}

async fn store_operation_request(
    endpoint: &str,
    request_type: FrameType,
    response_type: FrameType,
    object_id: &str,
    payload: Vec<u8>,
) -> anyhow::Result<StoreOperationOutcome> {
    let result = store_json_request(
        endpoint,
        request_type,
        response_type,
        Some(object_id.to_string()),
        payload,
    )
    .await?;
    let status = result.header.status.as_deref().unwrap_or("error");
    if status != "ok" {
        return Ok(StoreOperationOutcome {
            accepted: false,
            object_id: result
                .header
                .object_id
                .unwrap_or_else(|| object_id.to_string()),
            reason: result.header.reason.unwrap_or_default(),
        });
    }
    let response: StoreOperationResponse = serde_json::from_slice(&result.payload)?;
    Ok(StoreOperationOutcome {
        accepted: response.status == "ok",
        object_id: response.object_id,
        reason: response.reason,
    })
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

async fn synchronize_multipath_begin(
    stream: &mut TcpStream,
    transfer_id: &str,
) -> anyhow::Result<()> {
    let ping = FrameHeader::new(FrameType::Ping, transfer_id, 0);
    write_frame(stream, &ping, &[]).await?;
    let response = read_frame(stream).await?;
    if response.header.frame_type != FrameType::Pong {
        anyhow::bail!(
            "expected pong after multipath put_begin, got {:?}: {}",
            response.header.frame_type,
            response.header.reason.unwrap_or_default()
        );
    }
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

fn emit_retry_trace(
    trace: &Option<TraceSink>,
    transfer_id: &str,
    object_id: &str,
    chunk_index: u64,
    path_name: &str,
    retry_count: u32,
    reason: impl Into<String>,
) -> anyhow::Result<()> {
    emit_trace(
        trace,
        TraceEvent::new("chunk_retry")
            .transfer_id(transfer_id.to_string())
            .object_id(object_id.to_string())
            .chunk_index(chunk_index)
            .path_name(path_name.to_string())
            .bytes(retry_count as u64)
            .reason_code(reason),
    )
}

fn emit_transfer_failed(
    trace: &Option<TraceSink>,
    transfer_id: &str,
    object_id: &str,
    chunk_index: u64,
    reason: impl Into<String>,
) -> anyhow::Result<()> {
    emit_trace(
        trace,
        TraceEvent::new("transfer_failed")
            .transfer_id(transfer_id.to_string())
            .object_id(object_id.to_string())
            .chunk_index(chunk_index)
            .reason_code(reason),
    )
}

fn emit_path_status_trace(
    telemetry: &ClientTelemetry,
    transfer_id: &str,
    object_id: &str,
    chunk_index: u64,
    path_name: &str,
    status: PathStatus,
    reason: impl Into<String>,
) -> anyhow::Result<()> {
    let reason = reason.into();
    match status {
        PathStatus::Healthy => Ok(()),
        PathStatus::Degraded => emit_trace(
            &telemetry.trace,
            TraceEvent::new("path_degraded")
                .transfer_id(transfer_id.to_string())
                .object_id(object_id.to_string())
                .chunk_index(chunk_index)
                .path_name(path_name.to_string())
                .reason_code(reason),
        ),
        PathStatus::Dead => {
            telemetry.metrics.record_path_dead();
            emit_trace(
                &telemetry.trace,
                TraceEvent::new("path_dead")
                    .transfer_id(transfer_id.to_string())
                    .object_id(object_id.to_string())
                    .chunk_index(chunk_index)
                    .path_name(path_name.to_string())
                    .reason_code(reason),
            )
        }
    }
}

fn emit_trace(trace: &Option<TraceSink>, event: TraceEvent) -> anyhow::Result<()> {
    if let Some(trace) = trace {
        trace.emit(event)?;
    }
    Ok(())
}
