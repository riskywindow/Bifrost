use bifrostd::spool::Spool;
use bifrostd::transport::{
    chunk_bytes, iter_chunks, put_object_multipath_observed,
    put_object_multipath_observed_with_options, read_frame, serve_listener, write_frame, Chunk,
    ChunkManifest, ClientTelemetry, FrameHeader, FrameType, MultipathPutOptions, PathSpec,
    TraceSink, TransportMetrics, TRANSPORT_VERSION,
};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::net::{TcpListener, TcpStream};

const TEST_CHUNK_SIZE: usize = 64 * 1024;

struct Fixture {
    metadata_bytes: Vec<u8>,
    metadata: Value,
    payload: Vec<u8>,
}

struct Daemon {
    endpoint: String,
    task: tokio::task::JoinHandle<()>,
}

impl Drop for Daemon {
    fn drop(&mut self) {
        self.task.abort();
    }
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn native_fixture() -> Fixture {
    let root = repo_root().join("fixtures/native_valid");
    let meta_path = root.join("tiny_gpt_layer0_block0.meta.json");
    let payload_path = root.join("tiny_gpt_layer0_block0.payload.bin");
    let metadata_bytes = fs::read(meta_path).unwrap();
    let metadata = serde_json::from_slice(&metadata_bytes).unwrap();
    let payload = fs::read(payload_path).unwrap();
    Fixture {
        metadata_bytes,
        metadata,
        payload,
    }
}

fn object_id(fixture: &Fixture) -> &str {
    fixture.metadata["object_id"].as_str().unwrap()
}

fn temp_root(test_name: &str) -> PathBuf {
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "bifrostd-multipath-put-{test_name}-{}-{unique}",
        std::process::id()
    ))
}

async fn start_daemon(spool: Spool) -> Daemon {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = listener.local_addr().unwrap().to_string();
    let task = tokio::spawn(async move {
        let _ = serve_listener(listener, spool).await;
    });
    Daemon { endpoint, task }
}

async fn start_two_daemons(test_name: &str) -> (Spool, Daemon, Daemon, PathBuf) {
    let root = temp_root(test_name);
    fs::create_dir_all(&root).unwrap();
    let spool = Spool::new(&root);
    let first = start_daemon(spool.clone()).await;
    let second = start_daemon(spool.clone()).await;
    (spool, first, second, root)
}

fn cleanup(path: &Path) {
    if path.exists() {
        fs::remove_dir_all(path).unwrap();
    }
}

fn paths(first: &Daemon, second: &Daemon) -> Vec<PathSpec> {
    vec![
        PathSpec {
            name: "p0".to_string(),
            endpoint: first.endpoint.clone(),
        },
        PathSpec {
            name: "p1".to_string(),
            endpoint: second.endpoint.clone(),
        },
    ]
}

fn manifest_for(fixture: &Fixture, chunk_size: usize) -> ChunkManifest {
    let mut manifest = chunk_bytes(&fixture.payload, chunk_size).unwrap();
    manifest.object_id = Some(object_id(fixture).to_string());
    manifest
}

fn trace_events(path: &Path) -> Vec<Value> {
    fs::read_to_string(path)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}

fn event_types(events: &[Value]) -> Vec<&str> {
    events
        .iter()
        .map(|event| event["event_type"].as_str().unwrap())
        .collect()
}

fn retry_options(timeout_ms: u64, max_retries: u32) -> MultipathPutOptions {
    MultipathPutOptions {
        chunk_timeout: Duration::from_millis(timeout_ms),
        max_retries_per_chunk: max_retries,
        max_inflight_per_path: 16,
    }
}

#[tokio::test]
async fn multipath_put_over_two_listeners_commits_valid_object() {
    let (spool, first, second, root) = start_two_daemons("two-listeners-commit").await;
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();

    let outcome = put_object_multipath_observed(
        paths(&first, &second),
        fixture.metadata_bytes,
        fixture.payload.clone(),
        TEST_CHUNK_SIZE,
        None,
        ClientTelemetry::default(),
    )
    .await
    .unwrap();

    assert!(outcome.accepted);
    assert!(spool.has_object(&object_id));
    assert_eq!(spool.read_payload(&object_id).unwrap(), fixture.payload);
    cleanup(&root);
}

#[tokio::test]
async fn out_of_order_chunks_across_paths_still_commit() {
    let (spool, first, second, root) = start_two_daemons("out-of-order").await;
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();
    let manifest = manifest_for(&fixture, TEST_CHUNK_SIZE);
    let transfer_id = "multipath-out-of-order-001";
    let mut a = connect_and_hello(&first.endpoint, transfer_id).await;
    let mut b = connect_and_hello(&second.endpoint, transfer_id).await;
    send_put_begin(&mut a, transfer_id, &fixture.metadata_bytes, &manifest).await;

    let chunks = iter_chunks(&fixture.payload, manifest.chunk_size).unwrap();
    for chunk in chunks.iter().rev() {
        let stream = if chunk.info.chunk_index % 2 == 0 {
            &mut a
        } else {
            &mut b
        };
        send_chunk(stream, transfer_id, &object_id, &manifest, chunk, "manual")
            .await
            .unwrap();
    }
    send_commit(&mut b, transfer_id, &object_id, &manifest)
        .await
        .unwrap();

    assert!(spool.has_object(&object_id));
    assert_eq!(spool.read_payload(&object_id).unwrap(), fixture.payload);
    cleanup(&root);
}

#[tokio::test]
async fn client_trace_shows_chunks_sent_over_both_paths() {
    let (_spool, first, second, root) = start_two_daemons("trace-both-paths").await;
    let fixture = native_fixture();
    let trace_path = root.join("client-trace.jsonl");
    let telemetry = ClientTelemetry {
        metrics: TransportMetrics::default(),
        trace: Some(TraceSink::create(&trace_path).unwrap()),
    };

    let outcome = put_object_multipath_observed(
        paths(&first, &second),
        fixture.metadata_bytes,
        fixture.payload,
        TEST_CHUNK_SIZE,
        None,
        telemetry,
    )
    .await
    .unwrap();
    assert!(outcome.accepted);

    let events = trace_events(&trace_path);
    let chunk_paths = events
        .iter()
        .filter(|event| event["event_type"] == "chunk_sent")
        .map(|event| event["path_name"].as_str().unwrap())
        .collect::<Vec<_>>();
    assert!(chunk_paths.contains(&"p0"), "{chunk_paths:?}");
    assert!(chunk_paths.contains(&"p1"), "{chunk_paths:?}");
    cleanup(&root);
}

#[tokio::test]
async fn one_path_dead_before_transfer_starts_remaining_path_completes() {
    let root = temp_root("pre-transfer-dead-path");
    fs::create_dir_all(&root).unwrap();
    let spool = Spool::new(&root);
    let good = start_daemon(spool.clone()).await;
    let dead_endpoint = reserve_then_drop_endpoint().await;
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();

    let outcome = put_object_multipath_observed(
        vec![
            PathSpec {
                name: "dead".to_string(),
                endpoint: dead_endpoint,
            },
            PathSpec {
                name: "good".to_string(),
                endpoint: good.endpoint.clone(),
            },
        ],
        fixture.metadata_bytes,
        fixture.payload.clone(),
        TEST_CHUNK_SIZE,
        None,
        ClientTelemetry::default(),
    )
    .await
    .unwrap();

    assert!(outcome.accepted);
    assert_eq!(spool.read_payload(&object_id).unwrap(), fixture.payload);
    cleanup(&root);
}

#[tokio::test]
async fn one_path_fails_mid_transfer_remaining_path_retries_and_completes() {
    let root = temp_root("mid-transfer-failure");
    fs::create_dir_all(&root).unwrap();
    let spool = Spool::new(&root);
    let good = start_daemon(spool.clone()).await;
    let flaky = start_flaky_path().await;
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();
    let telemetry = ClientTelemetry {
        metrics: TransportMetrics::default(),
        trace: Some(TraceSink::create(root.join("retry-trace.jsonl")).unwrap()),
    };

    let outcome = put_object_multipath_observed(
        vec![
            PathSpec {
                name: "good".to_string(),
                endpoint: good.endpoint.clone(),
            },
            PathSpec {
                name: "flaky".to_string(),
                endpoint: flaky.endpoint.clone(),
            },
        ],
        fixture.metadata_bytes,
        fixture.payload.clone(),
        TEST_CHUNK_SIZE,
        None,
        telemetry.clone(),
    )
    .await
    .unwrap();

    assert!(outcome.accepted);
    assert_eq!(spool.read_payload(&object_id).unwrap(), fixture.payload);
    assert!(telemetry.metrics.snapshot().chunks_retried_total >= 1);
    cleanup(&root);
}

#[tokio::test]
async fn delayed_ack_triggers_retry_and_trace_events() {
    let root = temp_root("delayed-ack-retry");
    fs::create_dir_all(&root).unwrap();
    let spool = Spool::new(&root);
    let good = start_daemon(spool.clone()).await;
    let delayed = start_delayed_ack_path(Duration::from_millis(200)).await;
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();
    let trace_path = root.join("timeout-retry-trace.jsonl");
    let telemetry = ClientTelemetry {
        metrics: TransportMetrics::default(),
        trace: Some(TraceSink::create(&trace_path).unwrap()),
    };

    let outcome = put_object_multipath_observed_with_options(
        vec![
            PathSpec {
                name: "good".to_string(),
                endpoint: good.endpoint.clone(),
            },
            PathSpec {
                name: "slow".to_string(),
                endpoint: delayed.endpoint.clone(),
            },
        ],
        fixture.metadata_bytes,
        fixture.payload.clone(),
        TEST_CHUNK_SIZE,
        None,
        telemetry.clone(),
        retry_options(25, 3),
    )
    .await
    .unwrap();

    assert!(outcome.accepted);
    assert_eq!(spool.read_payload(&object_id).unwrap(), fixture.payload);
    let snapshot = telemetry.metrics.snapshot();
    assert!(snapshot.chunks_retried_total >= 1);
    assert!(snapshot.chunk_timeouts_total >= 1);
    let events = trace_events(&trace_path);
    let types = event_types(&events);
    assert!(types.contains(&"chunk_timeout"), "{types:?}");
    assert!(types.contains(&"chunk_retry"), "{types:?}");
    assert!(types.contains(&"path_degraded"), "{types:?}");
    assert!(types.contains(&"transfer_completed"), "{types:?}");
    cleanup(&root);
}

#[tokio::test]
async fn transfer_succeeds_when_one_of_two_paths_dies() {
    let root = temp_root("one-of-two-dies");
    fs::create_dir_all(&root).unwrap();
    let spool = Spool::new(&root);
    let good = start_daemon(spool.clone()).await;
    let dying = start_close_on_chunk_path().await;
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();
    let telemetry = ClientTelemetry {
        metrics: TransportMetrics::default(),
        trace: None,
    };

    let outcome = put_object_multipath_observed_with_options(
        vec![
            PathSpec {
                name: "good".to_string(),
                endpoint: good.endpoint.clone(),
            },
            PathSpec {
                name: "dead".to_string(),
                endpoint: dying.endpoint.clone(),
            },
        ],
        fixture.metadata_bytes,
        fixture.payload.clone(),
        TEST_CHUNK_SIZE,
        None,
        telemetry.clone(),
        retry_options(50, 3),
    )
    .await
    .unwrap();

    assert!(outcome.accepted);
    assert_eq!(spool.read_payload(&object_id).unwrap(), fixture.payload);
    assert!(telemetry.metrics.snapshot().chunks_retried_total >= 1);
    cleanup(&root);
}

#[tokio::test]
async fn transfer_fails_when_all_paths_die() {
    let first = start_begin_then_close_on_chunk_path().await;
    let second = start_close_on_chunk_path().await;
    let fixture = native_fixture();
    let telemetry = ClientTelemetry {
        metrics: TransportMetrics::default(),
        trace: None,
    };

    let error = put_object_multipath_observed_with_options(
        vec![
            PathSpec {
                name: "first".to_string(),
                endpoint: first.endpoint.clone(),
            },
            PathSpec {
                name: "second".to_string(),
                endpoint: second.endpoint.clone(),
            },
        ],
        fixture.metadata_bytes,
        fixture.payload,
        TEST_CHUNK_SIZE,
        None,
        telemetry.clone(),
        retry_options(25, 2),
    )
    .await
    .unwrap_err();

    assert!(!error.to_string().is_empty());
    let snapshot = telemetry.metrics.snapshot();
    assert!(snapshot.transfers_failed_total >= 1);
    assert!(snapshot.paths_dead_total >= 2);
}

#[tokio::test]
async fn max_retries_are_enforced() {
    let root = temp_root("max-retries");
    fs::create_dir_all(&root).unwrap();
    let spool = Spool::new(&root);
    let good = start_daemon(spool).await;
    let delayed = start_delayed_ack_path(Duration::from_millis(200)).await;
    let fixture = native_fixture();
    let trace_path = root.join("max-retries-trace.jsonl");
    let telemetry = ClientTelemetry {
        metrics: TransportMetrics::default(),
        trace: Some(TraceSink::create(&trace_path).unwrap()),
    };

    let error = put_object_multipath_observed_with_options(
        vec![
            PathSpec {
                name: "good".to_string(),
                endpoint: good.endpoint.clone(),
            },
            PathSpec {
                name: "slow".to_string(),
                endpoint: delayed.endpoint.clone(),
            },
        ],
        fixture.metadata_bytes,
        fixture.payload,
        TEST_CHUNK_SIZE,
        None,
        telemetry.clone(),
        retry_options(25, 0),
    )
    .await
    .unwrap_err();

    assert!(error.to_string().contains("exceeded max retries"));
    assert_eq!(telemetry.metrics.snapshot().chunk_timeouts_total, 1);
    let events = trace_events(&trace_path);
    let types = event_types(&events);
    assert!(types.contains(&"chunk_timeout"), "{types:?}");
    assert!(types.contains(&"transfer_failed"), "{types:?}");
    cleanup(&root);
}

#[tokio::test]
async fn duplicate_chunks_do_not_corrupt_committed_payload() {
    let (spool, first, second, root) = start_two_daemons("duplicate-chunks").await;
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();
    let manifest = manifest_for(&fixture, TEST_CHUNK_SIZE);
    let transfer_id = "multipath-duplicate-001";
    let mut a = connect_and_hello(&first.endpoint, transfer_id).await;
    let mut b = connect_and_hello(&second.endpoint, transfer_id).await;
    send_put_begin(&mut a, transfer_id, &fixture.metadata_bytes, &manifest).await;

    let chunks = iter_chunks(&fixture.payload, manifest.chunk_size).unwrap();
    send_chunk(&mut a, transfer_id, &object_id, &manifest, &chunks[0], "p0")
        .await
        .unwrap();
    send_chunk(&mut b, transfer_id, &object_id, &manifest, &chunks[0], "p1")
        .await
        .unwrap();
    for chunk in chunks.iter().skip(1) {
        send_chunk(&mut a, transfer_id, &object_id, &manifest, chunk, "p0")
            .await
            .unwrap();
    }
    send_commit(&mut b, transfer_id, &object_id, &manifest)
        .await
        .unwrap();

    assert_eq!(spool.read_payload(&object_id).unwrap(), fixture.payload);
    cleanup(&root);
}

#[tokio::test]
async fn conflicting_duplicate_chunk_is_rejected_and_not_committed() {
    let (spool, first, _second, root) = start_two_daemons("conflicting-duplicate").await;
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();
    let manifest = manifest_for(&fixture, TEST_CHUNK_SIZE);
    let transfer_id = "multipath-conflict-001";
    let mut stream = connect_and_hello(&first.endpoint, transfer_id).await;
    send_put_begin(&mut stream, transfer_id, &fixture.metadata_bytes, &manifest).await;

    let chunks = iter_chunks(&fixture.payload, manifest.chunk_size).unwrap();
    send_chunk(
        &mut stream,
        transfer_id,
        &object_id,
        &manifest,
        &chunks[0],
        "p0",
    )
    .await
    .unwrap();

    let mut header = chunk_header(transfer_id, &object_id, &manifest, &chunks[0], "p0");
    header.payload_hash = Some(chunks[1].info.hash.clone());
    write_frame(&mut stream, &header, chunks[1].bytes)
        .await
        .unwrap();
    let error = read_frame(&mut stream).await.unwrap();
    assert_eq!(error.header.frame_type, FrameType::Error);
    assert!(!spool.has_object(&object_id));
    cleanup(&root);
}

struct FlakyPath {
    endpoint: String,
    task: tokio::task::JoinHandle<()>,
}

impl Drop for FlakyPath {
    fn drop(&mut self) {
        self.task.abort();
    }
}

async fn start_flaky_path() -> FlakyPath {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = listener.local_addr().unwrap().to_string();
    let task = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let hello = read_frame(&mut stream).await.unwrap();
        assert_eq!(hello.header.frame_type, FrameType::Hello);
        let mut response = FrameHeader::new(FrameType::Hello, hello.header.transfer_id, 0);
        response.peer_role = Some("daemon".to_string());
        response.supported_versions = Some(vec![TRANSPORT_VERSION.to_string()]);
        write_frame(&mut stream, &response, &[]).await.unwrap();
        let frame = read_frame(&mut stream).await.unwrap();
        assert_eq!(frame.header.frame_type, FrameType::Chunk);
    });
    FlakyPath { endpoint, task }
}

async fn start_delayed_ack_path(delay: Duration) -> FlakyPath {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = listener.local_addr().unwrap().to_string();
    let task = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        fake_hello(&mut stream).await;
        let frame = read_frame(&mut stream).await.unwrap();
        assert_eq!(frame.header.frame_type, FrameType::Chunk);
        tokio::time::sleep(delay).await;
        let mut ack = FrameHeader::new(FrameType::ChunkAck, frame.header.transfer_id.clone(), 0);
        ack.object_id = frame.header.object_id.clone();
        ack.chunk_index = frame.header.chunk_index;
        ack.status = Some("accepted".to_string());
        ack.reason = Some(String::new());
        let _ = write_frame(&mut stream, &ack, &[]).await;
    });
    FlakyPath { endpoint, task }
}

async fn start_close_on_chunk_path() -> FlakyPath {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = listener.local_addr().unwrap().to_string();
    let task = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        fake_hello(&mut stream).await;
        let frame = read_frame(&mut stream).await.unwrap();
        assert_eq!(frame.header.frame_type, FrameType::Chunk);
    });
    FlakyPath { endpoint, task }
}

async fn start_begin_then_close_on_chunk_path() -> FlakyPath {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = listener.local_addr().unwrap().to_string();
    let task = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        fake_hello(&mut stream).await;
        let begin = read_frame(&mut stream).await.unwrap();
        assert_eq!(begin.header.frame_type, FrameType::PutBegin);
        let chunk = read_frame(&mut stream).await.unwrap();
        assert_eq!(chunk.header.frame_type, FrameType::Chunk);
    });
    FlakyPath { endpoint, task }
}

async fn fake_hello(stream: &mut TcpStream) {
    let hello = read_frame(stream).await.unwrap();
    assert_eq!(hello.header.frame_type, FrameType::Hello);
    let mut response = FrameHeader::new(FrameType::Hello, hello.header.transfer_id, 0);
    response.peer_role = Some("daemon".to_string());
    response.supported_versions = Some(vec![TRANSPORT_VERSION.to_string()]);
    write_frame(stream, &response, &[]).await.unwrap();
}

async fn reserve_then_drop_endpoint() -> String {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    listener.local_addr().unwrap().to_string()
}

async fn connect_and_hello(endpoint: &str, transfer_id: &str) -> TcpStream {
    let mut stream = TcpStream::connect(endpoint).await.unwrap();
    let mut hello = FrameHeader::new(FrameType::Hello, transfer_id, 0);
    hello.peer_role = Some("client".to_string());
    hello.supported_versions = Some(vec![TRANSPORT_VERSION.to_string()]);
    write_frame(&mut stream, &hello, &[]).await.unwrap();
    let response = read_frame(&mut stream).await.unwrap();
    assert_eq!(response.header.frame_type, FrameType::Hello);
    stream
}

async fn send_put_begin(
    stream: &mut TcpStream,
    transfer_id: &str,
    metadata_bytes: &[u8],
    manifest: &ChunkManifest,
) {
    let mut begin = FrameHeader::new(
        FrameType::PutBegin,
        transfer_id,
        metadata_bytes.len() as u64,
    );
    begin.object_id = manifest.object_id.clone();
    begin.total_chunks = Some(manifest.total_chunks);
    begin.descriptor_len = Some(metadata_bytes.len() as u64);
    begin.object_payload_len = Some(manifest.payload_len);
    begin.chunk_size = Some(manifest.chunk_size as u64);
    begin.payload_hash = Some(manifest.payload_hash.clone());
    begin.target_profile_id = Some("none".to_string());
    begin.flags = Some(BTreeMap::from([
        (
            "chunk_manifest".to_string(),
            serde_json::to_value(manifest).unwrap(),
        ),
        ("multipath".to_string(), json!(true)),
    ]));
    write_frame(stream, &begin, metadata_bytes).await.unwrap();
}

async fn send_chunk(
    stream: &mut TcpStream,
    transfer_id: &str,
    object_id: &str,
    manifest: &ChunkManifest,
    chunk: &Chunk<'_>,
    path_name: &str,
) -> anyhow::Result<()> {
    let header = chunk_header(transfer_id, object_id, manifest, chunk, path_name);
    write_frame(stream, &header, chunk.bytes).await?;
    let ack = read_frame(stream).await?;
    assert_eq!(ack.header.frame_type, FrameType::ChunkAck);
    let status = ack.header.status.as_deref().unwrap_or("rejected");
    assert!(status == "accepted" || status == "duplicate");
    Ok(())
}

fn chunk_header(
    transfer_id: &str,
    object_id: &str,
    manifest: &ChunkManifest,
    chunk: &Chunk<'_>,
    path_name: &str,
) -> FrameHeader {
    let mut header = FrameHeader::new(FrameType::Chunk, transfer_id, chunk.bytes.len() as u64);
    header.object_id = Some(object_id.to_string());
    header.chunk_index = Some(chunk.info.chunk_index);
    header.total_chunks = Some(manifest.total_chunks);
    header.chunk_offset = Some(chunk.info.offset);
    header.object_payload_len = Some(chunk.info.len);
    header.payload_hash = Some(chunk.info.hash.clone());
    header.flags = Some(BTreeMap::from([(
        "path_name".to_string(),
        json!(path_name),
    )]));
    header
}

async fn send_commit(
    stream: &mut TcpStream,
    transfer_id: &str,
    object_id: &str,
    manifest: &ChunkManifest,
) -> anyhow::Result<()> {
    let mut commit = FrameHeader::new(FrameType::PutCommit, transfer_id, 0);
    commit.object_id = Some(object_id.to_string());
    commit.total_chunks = Some(manifest.total_chunks);
    commit.object_payload_len = Some(manifest.payload_len);
    write_frame(stream, &commit, &[]).await?;
    let result = read_frame(stream).await?;
    assert_eq!(result.header.frame_type, FrameType::PutResult);
    assert_eq!(result.header.status.as_deref(), Some("committed"));
    Ok(())
}
