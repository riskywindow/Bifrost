use bifrostd::spool::Spool;
use bifrostd::transport::{
    chunk_bytes, get_object, put_object, put_validated_object, serve_listener_observed,
    ChunkManifest, TraceSink, TransportMetrics, DEFAULT_CHUNK_SIZE,
};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::net::TcpListener;

struct Fixture {
    metadata_bytes: Vec<u8>,
    metadata: Value,
    payload: Vec<u8>,
}

struct Daemon {
    endpoint: String,
    metrics: TransportMetrics,
    trace_path: PathBuf,
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
        "bifrostd-metrics-tracing-{test_name}-{}-{unique}",
        std::process::id()
    ))
}

async fn start_daemon(test_name: &str) -> (Daemon, PathBuf) {
    let root = temp_root(test_name);
    fs::create_dir_all(&root).unwrap();
    let spool = Spool::new(&root);
    let metrics = TransportMetrics::default();
    let trace_path = root.join("trace.jsonl");
    let trace = TraceSink::create(&trace_path).unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = listener.local_addr().unwrap().to_string();
    let server_spool = spool.clone();
    let server_metrics = metrics.clone();
    let task = tokio::spawn(async move {
        let _ = serve_listener_observed(listener, server_spool, server_metrics, Some(trace)).await;
    });
    (
        Daemon {
            endpoint,
            metrics,
            trace_path,
            task,
        },
        root,
    )
}

fn cleanup(path: &Path) {
    if path.exists() {
        fs::remove_dir_all(path).unwrap();
    }
}

fn manifest_for(fixture: &Fixture, payload: &[u8]) -> ChunkManifest {
    let mut manifest = chunk_bytes(payload, DEFAULT_CHUNK_SIZE).unwrap();
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

#[tokio::test]
async fn put_writes_expected_trace_events() {
    let (daemon, root) = start_daemon("put-writes-expected-trace-events").await;
    let fixture = native_fixture();

    let outcome = put_object(
        &daemon.endpoint,
        fixture.metadata_bytes,
        fixture.payload,
        DEFAULT_CHUNK_SIZE,
        None,
    )
    .await
    .unwrap();
    assert!(outcome.accepted);

    let events = trace_events(&daemon.trace_path);
    let types = event_types(&events);
    for expected in [
        "daemon_start",
        "server_put_begin",
        "chunk_received",
        "chunk_ack",
        "put_commit_started",
        "put_commit_accepted",
    ] {
        assert!(types.contains(&expected), "missing {expected}: {types:?}");
    }
    cleanup(&root);
}

#[tokio::test]
async fn get_writes_expected_trace_events() {
    let (daemon, root) = start_daemon("get-writes-expected-trace-events").await;
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();

    let put = put_object(
        &daemon.endpoint,
        fixture.metadata_bytes,
        fixture.payload,
        DEFAULT_CHUNK_SIZE,
        None,
    )
    .await
    .unwrap();
    assert!(put.accepted);

    let get = get_object(&daemon.endpoint, &object_id, DEFAULT_CHUNK_SIZE)
        .await
        .unwrap();
    assert!(get.found);

    let events = trace_events(&daemon.trace_path);
    let types = event_types(&events);
    for expected in ["get_begin", "get_chunk_sent", "get_completed"] {
        assert!(types.contains(&expected), "missing {expected}: {types:?}");
    }
    cleanup(&root);
}

#[tokio::test]
async fn rejected_put_trace_includes_reason_code() {
    let (daemon, root) = start_daemon("rejected-put-trace-includes-reason-code").await;
    let fixture = native_fixture();
    let mut corrupted = fixture.payload.clone();
    corrupted[0] ^= 0xff;
    let manifest = manifest_for(&fixture, &corrupted);

    let outcome = put_validated_object(
        &daemon.endpoint,
        fixture.metadata_bytes,
        corrupted,
        manifest,
    )
    .await
    .unwrap();
    assert!(!outcome.accepted);
    assert_eq!(outcome.reason, "payload_hash_mismatch");

    let events = trace_events(&daemon.trace_path);
    let rejected = events
        .iter()
        .find(|event| event["event_type"] == "put_commit_rejected")
        .unwrap();
    assert_eq!(rejected["reason_code"], "payload_hash_mismatch");
    cleanup(&root);
}

#[tokio::test]
async fn metrics_counters_increment_for_successful_and_failed_put() {
    let (daemon, root) =
        start_daemon("metrics-counters-increment-for-successful-and-failed-put").await;
    let fixture = native_fixture();

    let success = put_object(
        &daemon.endpoint,
        fixture.metadata_bytes.clone(),
        fixture.payload.clone(),
        DEFAULT_CHUNK_SIZE,
        None,
    )
    .await
    .unwrap();
    assert!(success.accepted);

    let mut corrupted = fixture.payload.clone();
    corrupted[0] ^= 0xff;
    let manifest = manifest_for(&fixture, &corrupted);
    let failed = put_validated_object(
        &daemon.endpoint,
        fixture.metadata_bytes,
        corrupted,
        manifest,
    )
    .await
    .unwrap();
    assert!(!failed.accepted);

    let snapshot = daemon.metrics.snapshot();
    assert_eq!(snapshot.transfers_started_total, 2);
    assert_eq!(snapshot.transfers_completed_total, 1);
    assert_eq!(snapshot.transfers_failed_total, 1);
    assert!(snapshot.bytes_received_total > 0);
    assert!(snapshot.chunks_received_total >= 2);
    assert_eq!(snapshot.chunks_retried_total, 0);
    assert_eq!(snapshot.validation_failures_total, 1);
    cleanup(&root);
}

#[tokio::test]
async fn trace_jsonl_lines_are_valid_json() {
    let (daemon, root) = start_daemon("trace-jsonl-lines-are-valid-json").await;
    let fixture = native_fixture();

    let outcome = put_object(
        &daemon.endpoint,
        fixture.metadata_bytes,
        fixture.payload,
        DEFAULT_CHUNK_SIZE,
        None,
    )
    .await
    .unwrap();
    assert!(outcome.accepted);

    let contents = fs::read_to_string(&daemon.trace_path).unwrap();
    assert!(!contents.trim().is_empty());
    for line in contents.lines() {
        let event: Value = serde_json::from_str(line).unwrap();
        assert!(event["timestamp_unix_ms"].as_u64().is_some());
        assert!(event["event_type"].as_str().is_some());
        assert_eq!(event["path_name"], "primary");
    }
    cleanup(&root);
}
