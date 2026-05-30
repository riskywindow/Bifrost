use crate::store::{EvictionPolicy, EvictionRequest, Store, StoreError};
use crate::transport::{
    chunk_bytes, iter_chunks, read_frame, write_frame, ChunkManifest, Frame, FrameHeader,
    FrameType, StoreEvictRequest, StoreEvictResponse, StoreInspectResponse, StoreLifecycleRequest,
    StoreListResponse, StoreManifestRequest, StoreManifestResponse, StoreObjectFilter,
    StoreObjectSummary, StoreOperationResponse, StoreStatsResponse, StoreTtlRequest, TraceEvent,
    TraceSink, TransportError, TransportMetrics, TransportResult, TRANSPORT_VERSION,
};
use std::collections::BTreeMap;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::net::{TcpListener, TcpStream};

#[derive(Debug, Clone)]
pub struct ServerConfig {
    pub listen: String,
    pub spool_root: PathBuf,
    pub trace_jsonl: Option<PathBuf>,
}

pub async fn serve(config: ServerConfig) -> anyhow::Result<()> {
    let listener = TcpListener::bind(&config.listen).await?;
    let trace = config.trace_jsonl.map(TraceSink::create).transpose()?;
    serve_listener_observed(
        listener,
        Store::open(config.spool_root)?,
        TransportMetrics::default(),
        trace,
    )
    .await
}

pub async fn serve_listener<S>(listener: TcpListener, store: S) -> anyhow::Result<()>
where
    S: Into<Store>,
{
    serve_listener_observed(listener, store, TransportMetrics::default(), None).await
}

pub async fn serve_listener_observed<S>(
    listener: TcpListener,
    store: S,
    metrics: TransportMetrics,
    trace: Option<TraceSink>,
) -> anyhow::Result<()>
where
    S: Into<Store>,
{
    let store = store.into();
    let _ = listener.local_addr()?;
    emit_trace(&trace, TraceEvent::new("daemon_start"));
    loop {
        let (stream, peer) = listener.accept().await?;
        let store = store.clone();
        let metrics = metrics.clone();
        let trace = trace.clone();
        tokio::spawn(async move {
            if let Err(error) =
                handle_connection_observed(stream, store, peer, metrics, trace).await
            {
                eprintln!("bifrost-daemon: connection {peer} ended with error: {error}");
            }
        });
    }
}

pub async fn handle_connection(
    stream: TcpStream,
    store: Store,
    peer: SocketAddr,
) -> anyhow::Result<()> {
    handle_connection_observed(stream, store, peer, TransportMetrics::default(), None).await
}

pub async fn handle_connection_observed(
    mut stream: TcpStream,
    store: Store,
    _peer: SocketAddr,
    metrics: TransportMetrics,
    trace: Option<TraceSink>,
) -> anyhow::Result<()> {
    let hello = read_frame(&mut stream).await?;
    if hello.header.frame_type != FrameType::Hello {
        metrics.record_transfer_failed();
        emit_trace(
            &trace,
            TraceEvent::new("transfer_error")
                .transfer_id(hello.header.transfer_id.clone())
                .reason_code("expected_hello"),
        );
        send_error(
            &mut stream,
            &hello.header.transfer_id,
            "expected hello as first frame",
            &metrics,
            &trace,
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
                    let _ = store.abort_put(&transfer_id);
                }
                return Ok(());
            }
            Err(error) => return Err(error.into()),
        };

        match frame.header.frame_type {
            FrameType::PutBegin => {
                let transfer_id = frame.header.transfer_id.clone();
                metrics.record_transfer_started();
                metrics.record_bytes_received(frame.payload.len() as u64);
                emit_trace(
                    &trace,
                    TraceEvent::new("server_put_begin")
                        .transfer_id(transfer_id.clone())
                        .maybe_object_id(frame.header.object_id.as_deref())
                        .bytes(frame.payload.len() as u64),
                );
                let result = begin_put(&store, &frame);
                match result {
                    Ok(()) => {
                        if !is_multipath_begin(&frame) {
                            active_transfer = Some(transfer_id);
                        }
                    }
                    Err(error) => {
                        metrics.record_transfer_failed();
                        emit_trace(
                            &trace,
                            TraceEvent::new("transfer_error")
                                .transfer_id(frame.header.transfer_id.clone())
                                .maybe_object_id(frame.header.object_id.as_deref())
                                .reason_code(error.to_string()),
                        );
                        send_error(
                            &mut stream,
                            &frame.header.transfer_id,
                            &error.to_string(),
                            &metrics,
                            &trace,
                        )
                        .await?;
                        return Ok(());
                    }
                }
            }
            FrameType::Chunk => {
                let status = match write_chunk(&store, &frame) {
                    Ok(()) => ("accepted", ""),
                    Err(error) => {
                        metrics.record_transfer_failed();
                        emit_trace(
                            &trace,
                            TraceEvent::new("transfer_error")
                                .transfer_id(frame.header.transfer_id.clone())
                                .maybe_object_id(frame.header.object_id.as_deref())
                                .chunk_index(frame.header.chunk_index.unwrap_or_default())
                                .reason_code(error.to_string()),
                        );
                        send_error(
                            &mut stream,
                            &frame.header.transfer_id,
                            &error.to_string(),
                            &metrics,
                            &trace,
                        )
                        .await?;
                        let _ = store.abort_put(&frame.header.transfer_id);
                        return Ok(());
                    }
                };
                metrics.record_chunk_received(frame.payload.len() as u64);
                emit_trace(
                    &trace,
                    TraceEvent::new("chunk_received")
                        .transfer_id(frame.header.transfer_id.clone())
                        .maybe_object_id(frame.header.object_id.as_deref())
                        .chunk_index(frame.header.chunk_index.unwrap_or_default())
                        .bytes(frame.payload.len() as u64)
                        .path_name(path_name_from_frame(&frame)),
                );
                send_chunk_ack(&mut stream, &frame, status.0, status.1, &trace).await?;
            }
            FrameType::PutCommit => {
                send_put_result(&mut stream, &store, &frame, &metrics, &trace).await?;
                return Ok(());
            }
            FrameType::HasRequest => {
                send_has_result(&mut stream, &store, &frame).await?;
                return Ok(());
            }
            FrameType::GetBegin => {
                metrics.record_transfer_started();
                emit_trace(
                    &trace,
                    TraceEvent::new("get_begin")
                        .transfer_id(frame.header.transfer_id.clone())
                        .maybe_object_id(frame.header.object_id.as_deref()),
                );
                send_get_response(&mut stream, &store, &frame, &metrics, &trace).await?;
                return Ok(());
            }
            FrameType::ListRequest => {
                send_list_result(&mut stream, &store, &frame).await?;
                return Ok(());
            }
            FrameType::InspectRequest => {
                send_inspect_result(&mut stream, &store, &frame).await?;
                return Ok(());
            }
            FrameType::QueryRequest => {
                send_query_result(&mut stream, &store, &frame).await?;
                return Ok(());
            }
            FrameType::StatsRequest => {
                send_stats_result(&mut stream, &store, &frame).await?;
                return Ok(());
            }
            FrameType::PinRequest => {
                send_pin_result(&mut stream, &store, &frame).await?;
                return Ok(());
            }
            FrameType::UnpinRequest => {
                send_unpin_result(&mut stream, &store, &frame).await?;
                return Ok(());
            }
            FrameType::TtlRequest => {
                send_ttl_result(&mut stream, &store, &frame).await?;
                return Ok(());
            }
            FrameType::LifecycleRequest => {
                send_lifecycle_result(&mut stream, &store, &frame).await?;
                return Ok(());
            }
            FrameType::EvictRequest => {
                send_evict_result(&mut stream, &store, &frame).await?;
                return Ok(());
            }
            FrameType::ManifestRequest => {
                send_manifest_result(&mut stream, &store, &frame).await?;
                return Ok(());
            }
            FrameType::FsckRequest => {
                send_fsck_result(&mut stream, &store, &frame).await?;
                return Ok(());
            }
            _ => {
                metrics.record_transfer_failed();
                send_error(
                    &mut stream,
                    &frame.header.transfer_id,
                    &format!("unexpected frame type {:?}", frame.header.frame_type),
                    &metrics,
                    &trace,
                )
                .await?;
                return Ok(());
            }
        }
    }
}

async fn send_fsck_result(
    stream: &mut TcpStream,
    store: &Store,
    request: &Frame,
) -> TransportResult<()> {
    let operation: crate::transport::StoreFsckRequest = if request.payload.is_empty() {
        crate::transport::StoreFsckRequest {
            mode: crate::store::FsckMode::Check,
        }
    } else {
        serde_json::from_slice(&request.payload)?
    };
    match store.fsck(operation.mode) {
        Ok(result) => {
            let payload = serde_json::to_vec(&crate::transport::StoreFsckResponse { result })?;
            let mut header = FrameHeader::new(
                FrameType::FsckResult,
                request.header.transfer_id.clone(),
                payload.len() as u64,
            );
            header.status = Some("ok".to_string());
            header.reason = Some(String::new());
            write_frame(stream, &header, &payload).await
        }
        Err(error) => {
            send_store_result_error(
                stream,
                FrameType::FsckResult,
                &request.header.transfer_id,
                &error.to_string(),
            )
            .await
        }
    }
}

async fn send_manifest_result(
    stream: &mut TcpStream,
    store: &Store,
    request: &Frame,
) -> TransportResult<()> {
    let operation: StoreManifestRequest = serde_json::from_slice(&request.payload)?;
    let response = match operation {
        StoreManifestRequest::CreatePrefix {
            model_hash,
            tokenizer_hash,
            rope_config_hash,
            prefix_hash,
            token_range_start,
            token_range_end,
        } => store
            .create_prefix_manifest(
                model_hash,
                tokenizer_hash,
                rope_config_hash,
                prefix_hash,
                token_range_start,
                token_range_end,
            )
            .map(|manifest| StoreManifestResponse {
                status: "ok".to_string(),
                reason: String::new(),
                manifest: Some(crate::store::ManifestInspection {
                    manifest,
                    members: Vec::new(),
                }),
                manifests: Vec::new(),
                completeness: None,
            }),
        StoreManifestRequest::AddMember {
            manifest_id,
            object_id,
            required,
        } => store
            .add_manifest_member(&manifest_id, &object_id, required)
            .and_then(|_| store.get_manifest(&manifest_id))
            .map(|manifest| StoreManifestResponse {
                status: "ok".to_string(),
                reason: String::new(),
                manifest: Some(manifest),
                manifests: Vec::new(),
                completeness: None,
            }),
        StoreManifestRequest::RemoveMember {
            manifest_id,
            object_id,
        } => store
            .remove_manifest_member(&manifest_id, &object_id)
            .and_then(|_| store.get_manifest(&manifest_id))
            .map(|manifest| StoreManifestResponse {
                status: "ok".to_string(),
                reason: String::new(),
                manifest: Some(manifest),
                manifests: Vec::new(),
                completeness: None,
            }),
        StoreManifestRequest::Inspect { manifest_id } => {
            store
                .get_manifest(&manifest_id)
                .map(|manifest| StoreManifestResponse {
                    status: "ok".to_string(),
                    reason: String::new(),
                    manifest: Some(manifest),
                    manifests: Vec::new(),
                    completeness: None,
                })
        }
        StoreManifestRequest::List { filter } => {
            store
                .list_manifests(&filter)
                .map(|manifests| StoreManifestResponse {
                    status: "ok".to_string(),
                    reason: String::new(),
                    manifest: None,
                    manifests,
                    completeness: None,
                })
        }
        StoreManifestRequest::Check { manifest_id } => store
            .check_manifest_completeness(&manifest_id)
            .and_then(|completeness| {
                store
                    .get_manifest(&manifest_id)
                    .map(|manifest| (manifest, completeness))
            })
            .map(|(manifest, completeness)| StoreManifestResponse {
                status: "ok".to_string(),
                reason: String::new(),
                manifest: Some(manifest),
                manifests: Vec::new(),
                completeness: Some(completeness),
            }),
        StoreManifestRequest::Pin { manifest_id } => store
            .pin_manifest(&manifest_id)
            .and_then(|_| store.get_manifest(&manifest_id))
            .map(|manifest| StoreManifestResponse {
                status: "ok".to_string(),
                reason: String::new(),
                manifest: Some(manifest),
                manifests: Vec::new(),
                completeness: None,
            }),
        StoreManifestRequest::Unpin { manifest_id } => store
            .unpin_manifest(&manifest_id)
            .and_then(|_| store.get_manifest(&manifest_id))
            .map(|manifest| StoreManifestResponse {
                status: "ok".to_string(),
                reason: String::new(),
                manifest: Some(manifest),
                manifests: Vec::new(),
                completeness: None,
            }),
    };

    match response {
        Ok(response) => {
            let payload = serde_json::to_vec(&response)?;
            let mut result = FrameHeader::new(
                FrameType::ManifestResult,
                request.header.transfer_id.clone(),
                payload.len() as u64,
            );
            result.status = Some("ok".to_string());
            result.reason = Some(String::new());
            write_frame(stream, &result, &payload).await
        }
        Err(error) => {
            send_store_result_error(
                stream,
                FrameType::ManifestResult,
                &request.header.transfer_id,
                &error.to_string(),
            )
            .await
        }
    }
}

async fn send_evict_result(
    stream: &mut TcpStream,
    store: &Store,
    request: &Frame,
) -> TransportResult<()> {
    let operation: StoreEvictRequest = serde_json::from_slice(&request.payload)?;
    let policy = match operation.policy.parse::<EvictionPolicy>() {
        Ok(policy) => policy,
        Err(error) => {
            return send_store_result_error(
                stream,
                FrameType::EvictResult,
                &request.header.transfer_id,
                &error,
            )
            .await;
        }
    };
    let eviction_request = EvictionRequest {
        policy,
        target_bytes: operation.target_bytes,
        max_objects: operation.max_objects,
        dry_run: operation.dry_run,
        now_unix_ms: operation.now_unix_ms.unwrap_or_else(now_unix_ms),
    };
    match store.evict(eviction_request) {
        Ok(report) => {
            let payload = serde_json::to_vec(&StoreEvictResponse { report })?;
            let mut result = FrameHeader::new(
                FrameType::EvictResult,
                request.header.transfer_id.clone(),
                payload.len() as u64,
            );
            result.status = Some("ok".to_string());
            result.reason = Some(String::new());
            write_frame(stream, &result, &payload).await
        }
        Err(error) => {
            send_store_result_error(
                stream,
                FrameType::EvictResult,
                &request.header.transfer_id,
                &error.to_string(),
            )
            .await
        }
    }
}

async fn send_pin_result(
    stream: &mut TcpStream,
    store: &Store,
    request: &Frame,
) -> TransportResult<()> {
    let object_id = request.header.object_id.clone().unwrap_or_default();
    send_operation_result(
        stream,
        FrameType::PinResult,
        request,
        &object_id,
        store.pin_object(&object_id),
    )
    .await
}

async fn send_unpin_result(
    stream: &mut TcpStream,
    store: &Store,
    request: &Frame,
) -> TransportResult<()> {
    let object_id = request.header.object_id.clone().unwrap_or_default();
    send_operation_result(
        stream,
        FrameType::UnpinResult,
        request,
        &object_id,
        store.unpin_object(&object_id),
    )
    .await
}

async fn send_ttl_result(
    stream: &mut TcpStream,
    store: &Store,
    request: &Frame,
) -> TransportResult<()> {
    let object_id = request.header.object_id.clone().unwrap_or_default();
    let operation: StoreTtlRequest = serde_json::from_slice(&request.payload)?;
    let result = match operation {
        StoreTtlRequest::Set { expires_at_unix_ms } => {
            store.set_ttl(&object_id, expires_at_unix_ms)
        }
        StoreTtlRequest::Clear => store.clear_ttl(&object_id),
    };
    send_operation_result(stream, FrameType::TtlResult, request, &object_id, result).await
}

async fn send_lifecycle_result(
    stream: &mut TcpStream,
    store: &Store,
    request: &Frame,
) -> TransportResult<()> {
    let object_id = request.header.object_id.clone().unwrap_or_default();
    let operation: StoreLifecycleRequest = serde_json::from_slice(&request.payload)?;
    let result = match operation {
        StoreLifecycleRequest::Quarantine { reason } => store.mark_quarantined(&object_id, &reason),
        StoreLifecycleRequest::MarkVerified => store.mark_verified(&object_id),
    };
    send_operation_result(
        stream,
        FrameType::LifecycleResult,
        request,
        &object_id,
        result,
    )
    .await
}

async fn send_operation_result(
    stream: &mut TcpStream,
    frame_type: FrameType,
    request: &Frame,
    object_id: &str,
    operation: crate::store::errors::StoreResult<()>,
) -> TransportResult<()> {
    match operation {
        Ok(()) => {
            let payload = serde_json::to_vec(&StoreOperationResponse::ok(object_id))?;
            let mut result = FrameHeader::new(
                frame_type,
                request.header.transfer_id.clone(),
                payload.len() as u64,
            );
            result.object_id = Some(object_id.to_string());
            result.status = Some("ok".to_string());
            result.reason = Some(String::new());
            write_frame(stream, &result, &payload).await
        }
        Err(error) => {
            let mut result = FrameHeader::new(frame_type, request.header.transfer_id.clone(), 0);
            result.object_id = Some(object_id.to_string());
            result.status = Some("error".to_string());
            result.reason = Some(error.to_string());
            write_frame(stream, &result, &[]).await
        }
    }
}

async fn send_list_result(
    stream: &mut TcpStream,
    store: &Store,
    request: &Frame,
) -> TransportResult<()> {
    let filter = parse_filter_payload(request)
        .map_err(|error| TransportError::Protocol(error.to_string()))?;
    match store_object_summaries(store, &filter) {
        Ok(objects) => {
            let payload = serde_json::to_vec(&StoreListResponse { objects })?;
            let mut result = FrameHeader::new(
                FrameType::ListResult,
                request.header.transfer_id.clone(),
                payload.len() as u64,
            );
            result.status = Some("ok".to_string());
            result.reason = Some(String::new());
            write_frame(stream, &result, &payload).await
        }
        Err(error) => {
            send_store_result_error(
                stream,
                FrameType::ListResult,
                &request.header.transfer_id,
                &error.to_string(),
            )
            .await
        }
    }
}

async fn send_query_result(
    stream: &mut TcpStream,
    store: &Store,
    request: &Frame,
) -> TransportResult<()> {
    let filter = parse_filter_payload(request)
        .map_err(|error| TransportError::Protocol(error.to_string()))?;
    match store_object_summaries(store, &filter) {
        Ok(objects) => {
            let payload = serde_json::to_vec(&StoreListResponse { objects })?;
            let mut result = FrameHeader::new(
                FrameType::QueryResult,
                request.header.transfer_id.clone(),
                payload.len() as u64,
            );
            result.status = Some("ok".to_string());
            result.reason = Some(String::new());
            write_frame(stream, &result, &payload).await
        }
        Err(error) => {
            send_store_result_error(
                stream,
                FrameType::QueryResult,
                &request.header.transfer_id,
                &error.to_string(),
            )
            .await
        }
    }
}

async fn send_inspect_result(
    stream: &mut TcpStream,
    store: &Store,
    request: &Frame,
) -> TransportResult<()> {
    let object_id = request.header.object_id.clone().unwrap_or_default();
    let response = match store.inspect_object(&object_id) {
        Ok(inspection) if inspection.servable => StoreInspectResponse::found(&inspection),
        Ok(_) => StoreInspectResponse::miss("not_found"),
        Err(StoreError::NotFound(_)) => StoreInspectResponse::miss("not_found"),
        Err(error) => {
            return send_store_result_error(
                stream,
                FrameType::InspectResult,
                &request.header.transfer_id,
                &error.to_string(),
            )
            .await;
        }
    };
    let status = if response.found { "ok" } else { "miss" };
    let reason = response.reason.clone().unwrap_or_default();
    let payload = serde_json::to_vec(&response)?;
    let mut result = FrameHeader::new(
        FrameType::InspectResult,
        request.header.transfer_id.clone(),
        payload.len() as u64,
    );
    result.object_id = Some(object_id);
    result.status = Some(status.to_string());
    result.reason = Some(reason);
    write_frame(stream, &result, &payload).await
}

async fn send_stats_result(
    stream: &mut TcpStream,
    store: &Store,
    request: &Frame,
) -> TransportResult<()> {
    match store.stats() {
        Ok(stats) => {
            let payload = serde_json::to_vec(&StoreStatsResponse::from(stats))?;
            let mut result = FrameHeader::new(
                FrameType::StatsResult,
                request.header.transfer_id.clone(),
                payload.len() as u64,
            );
            result.status = Some("ok".to_string());
            result.reason = Some(String::new());
            write_frame(stream, &result, &payload).await
        }
        Err(error) => {
            send_store_result_error(
                stream,
                FrameType::StatsResult,
                &request.header.transfer_id,
                &error.to_string(),
            )
            .await
        }
    }
}

fn parse_filter_payload(frame: &Frame) -> anyhow::Result<StoreObjectFilter> {
    if frame.payload.is_empty() {
        Ok(StoreObjectFilter::default())
    } else {
        Ok(serde_json::from_slice(&frame.payload)?)
    }
}

fn store_object_summaries(
    store: &Store,
    filter: &StoreObjectFilter,
) -> anyhow::Result<Vec<StoreObjectSummary>> {
    let records = store.list_objects(&filter.to_list_filter()?)?;
    let mut objects = Vec::new();
    for record in records {
        let Ok(inspection) = store.inspect_object(&record.object_id) else {
            continue;
        };
        if inspection.servable {
            objects.push(StoreObjectSummary::from_parts(
                &inspection.record,
                &inspection.compatibility,
            ));
        }
    }
    Ok(objects)
}

async fn send_store_result_error(
    stream: &mut TcpStream,
    frame_type: FrameType,
    transfer_id: &str,
    reason: &str,
) -> TransportResult<()> {
    let mut result = FrameHeader::new(frame_type, transfer_id, 0);
    result.status = Some("error".to_string());
    result.reason = Some(reason.to_string());
    write_frame(stream, &result, &[]).await
}

fn is_multipath_begin(frame: &Frame) -> bool {
    frame
        .header
        .flags
        .as_ref()
        .and_then(|flags| flags.get("multipath"))
        .and_then(serde_json::Value::as_bool)
        .unwrap_or(false)
}

fn path_name_from_frame(frame: &Frame) -> String {
    frame
        .header
        .flags
        .as_ref()
        .and_then(|flags| flags.get("path_name"))
        .and_then(serde_json::Value::as_str)
        .unwrap_or("primary")
        .to_string()
}

fn begin_put(store: &Store, frame: &Frame) -> anyhow::Result<()> {
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
    store.begin_put(&frame.header.transfer_id, &frame.payload, &manifest)?;
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

fn write_chunk(store: &Store, frame: &Frame) -> anyhow::Result<()> {
    let chunk_index = frame
        .header
        .chunk_index
        .ok_or_else(|| anyhow::anyhow!("chunk missing chunk_index"))?;
    store.write_chunk(&frame.header.transfer_id, chunk_index, &frame.payload)?;
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
    trace: &Option<TraceSink>,
) -> TransportResult<()> {
    let mut ack = FrameHeader::new(FrameType::ChunkAck, request.header.transfer_id.clone(), 0);
    ack.object_id = request.header.object_id.clone();
    ack.chunk_index = request.header.chunk_index;
    ack.status = Some(status.to_string());
    ack.reason = Some(reason.to_string());
    let path_name = path_name_from_frame(request);
    ack.flags = Some(BTreeMap::from([(
        "path_name".to_string(),
        serde_json::json!(path_name.clone()),
    )]));
    write_frame(stream, &ack, &[]).await?;
    emit_trace(
        trace,
        TraceEvent::new("chunk_ack")
            .transfer_id(request.header.transfer_id.clone())
            .maybe_object_id(request.header.object_id.as_deref())
            .chunk_index(request.header.chunk_index.unwrap_or_default())
            .path_name(path_name)
            .reason_code(reason),
    );
    Ok(())
}

async fn send_put_result(
    stream: &mut TcpStream,
    store: &Store,
    request: &Frame,
    metrics: &TransportMetrics,
    trace: &Option<TraceSink>,
) -> TransportResult<()> {
    let object_id = request.header.object_id.clone().unwrap_or_default();
    emit_trace(
        trace,
        TraceEvent::new("put_commit_started")
            .transfer_id(request.header.transfer_id.clone())
            .object_id(object_id.clone()),
    );
    let (status, reason) = match store.commit_put(&request.header.transfer_id, None) {
        Ok(_) => ("committed", String::new()),
        Err(StoreError::Integrity(reason)) => {
            metrics.record_validation_failure();
            ("rejected", reason)
        }
        Err(error) => {
            metrics.record_commit_failure();
            ("rejected", error.to_string())
        }
    };
    if status == "committed" {
        metrics.record_transfer_completed();
        emit_trace(
            trace,
            TraceEvent::new("put_commit_accepted")
                .transfer_id(request.header.transfer_id.clone())
                .object_id(object_id.clone()),
        );
    } else {
        metrics.record_transfer_failed();
        emit_trace(
            trace,
            TraceEvent::new("put_commit_rejected")
                .transfer_id(request.header.transfer_id.clone())
                .object_id(object_id.clone())
                .reason_code(reason.clone()),
        );
    }

    let mut result = FrameHeader::new(FrameType::PutResult, request.header.transfer_id.clone(), 0);
    result.object_id = Some(object_id);
    result.status = Some(status.to_string());
    result.reason = Some(reason);
    write_frame(stream, &result, &[]).await
}

async fn send_has_result(
    stream: &mut TcpStream,
    store: &Store,
    request: &Frame,
) -> TransportResult<()> {
    let object_id = request.header.object_id.clone().unwrap_or_default();
    let present = store.has_object(&object_id).unwrap_or(false);
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
    store: &Store,
    request: &Frame,
    metrics: &TransportMetrics,
    trace: &Option<TraceSink>,
) -> TransportResult<()> {
    let object_id = request.header.object_id.clone().unwrap_or_default();
    let stored = match store.get_object(&object_id) {
        Ok(stored) => stored,
        Err(StoreError::NotFound(_)) => {
            send_get_result_miss(stream, request, &object_id, "not_found").await?;
            metrics.record_transfer_failed();
            emit_trace(
                trace,
                TraceEvent::new("transfer_error")
                    .transfer_id(request.header.transfer_id.clone())
                    .object_id(object_id)
                    .reason_code("not_found"),
            );
            return Ok(());
        }
        Err(error) => {
            send_get_result_miss(stream, request, &object_id, &error.to_string()).await?;
            metrics.record_transfer_failed();
            emit_trace(
                trace,
                TraceEvent::new("transfer_error")
                    .transfer_id(request.header.transfer_id.clone())
                    .object_id(object_id)
                    .reason_code(error.to_string()),
            );
            return Ok(());
        }
    };
    let metadata = stored.metadata;
    let payload = stored.payload;

    let chunk_size = request
        .header
        .chunk_size
        .map(|value| value as usize)
        .unwrap_or(crate::transport::DEFAULT_CHUNK_SIZE);
    let mut manifest = match chunk_bytes(&payload, chunk_size) {
        Ok(manifest) => manifest,
        Err(error) => {
            send_get_result_miss(stream, request, &object_id, &error.to_string()).await?;
            metrics.record_transfer_failed();
            emit_trace(
                trace,
                TraceEvent::new("transfer_error")
                    .transfer_id(request.header.transfer_id.clone())
                    .object_id(object_id)
                    .reason_code(error.to_string()),
            );
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
        metrics.record_chunk_sent(chunk.bytes.len() as u64);
        emit_trace(
            trace,
            TraceEvent::new("get_chunk_sent")
                .transfer_id(request.header.transfer_id.clone())
                .object_id(object_id.clone())
                .chunk_index(chunk.info.chunk_index)
                .bytes(chunk.bytes.len() as u64),
        );
    }

    let mut done = FrameHeader::new(FrameType::GetResult, request.header.transfer_id.clone(), 0);
    done.object_id = Some(object_id.clone());
    done.status = Some("success".to_string());
    done.reason = Some(String::new());
    done.descriptor_len = Some(metadata.len() as u64);
    done.object_payload_len = Some(manifest.payload_len);
    done.chunk_size = Some(manifest.chunk_size as u64);
    done.total_chunks = Some(manifest.total_chunks);
    done.payload_hash = Some(manifest.payload_hash);
    write_frame(stream, &done, &[]).await?;
    metrics.record_transfer_completed();
    emit_trace(
        trace,
        TraceEvent::new("get_completed")
            .transfer_id(request.header.transfer_id.clone())
            .object_id(object_id)
            .bytes(manifest.payload_len),
    );
    Ok(())
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
    metrics: &TransportMetrics,
    trace: &Option<TraceSink>,
) -> TransportResult<()> {
    let mut error = FrameHeader::new(FrameType::Error, transfer_id, 0);
    error.status = Some("rejected".to_string());
    error.reason = Some(reason.to_string());
    error.flags = Some(BTreeMap::new());
    metrics.record_protocol_error();
    emit_trace(
        trace,
        TraceEvent::new("transfer_error")
            .transfer_id(transfer_id.to_string())
            .reason_code(reason),
    );
    write_frame(stream, &error, &[]).await
}

fn emit_trace(trace: &Option<TraceSink>, event: TraceEvent) {
    if let Some(trace) = trace {
        if let Err(error) = trace.emit(event) {
            eprintln!("bifrost-daemon: trace write failed: {error}");
        }
    }
}

fn now_unix_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock is before unix epoch")
        .as_millis() as i64
}
