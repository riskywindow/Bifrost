use bifrostd::cache::validate_object;
use bifrostd::spool::Spool;
use bifrostd::transport::{
    chunk_bytes, put_object, put_validated_object, read_frame, serve_listener, write_frame,
    ChunkManifest, FrameHeader, FrameType, DEFAULT_CHUNK_SIZE,
};
use serde_json::Value;
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::net::{TcpListener, TcpStream};

struct Fixture {
    metadata_bytes: Vec<u8>,
    metadata: Value,
    payload: Vec<u8>,
    meta_path: PathBuf,
    payload_path: PathBuf,
}

struct Daemon {
    endpoint: String,
    spool: Spool,
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
    let metadata_bytes = fs::read(&meta_path).unwrap();
    let metadata = serde_json::from_slice(&metadata_bytes).unwrap();
    let payload = fs::read(&payload_path).unwrap();
    Fixture {
        metadata_bytes,
        metadata,
        payload,
        meta_path,
        payload_path,
    }
}

fn temp_root(test_name: &str) -> PathBuf {
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "bifrostd-put-{test_name}-{}-{unique}",
        std::process::id()
    ))
}

async fn start_daemon(test_name: &str) -> (Daemon, PathBuf) {
    let root = temp_root(test_name);
    let spool = Spool::new(&root);
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = listener.local_addr().unwrap().to_string();
    let server_spool = spool.clone();
    let task = tokio::spawn(async move {
        let _ = serve_listener(listener, server_spool).await;
    });
    (
        Daemon {
            endpoint,
            spool,
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

fn manifest_for(metadata: &Value, payload: &[u8]) -> ChunkManifest {
    let mut manifest = chunk_bytes(payload, DEFAULT_CHUNK_SIZE).unwrap();
    manifest.object_id = Some(metadata["object_id"].as_str().unwrap().to_string());
    manifest
}

#[tokio::test]
async fn valid_native_fixture_put_commits_payload() {
    let (daemon, root) = start_daemon("valid-native-fixture-put-commits-payload").await;
    let fixture = native_fixture();
    let object_id = fixture.metadata["object_id"].as_str().unwrap();

    let outcome = put_object(
        &daemon.endpoint,
        fixture.metadata_bytes.clone(),
        fixture.payload.clone(),
        DEFAULT_CHUNK_SIZE,
        None,
    )
    .await
    .unwrap();

    assert!(outcome.accepted);
    assert!(daemon.spool.has_object(object_id));
    assert_eq!(
        daemon.spool.read_payload(object_id).unwrap(),
        fixture.payload
    );
    cleanup(&root);
}

#[tokio::test]
async fn corrupted_payload_with_unchanged_metadata_is_rejected() {
    let (daemon, root) =
        start_daemon("corrupted-payload-with-unchanged-metadata-is-rejected").await;
    let fixture = native_fixture();
    let object_id = fixture.metadata["object_id"].as_str().unwrap();
    let mut corrupted = fixture.payload.clone();
    corrupted[0] ^= 0xff;
    let manifest = manifest_for(&fixture.metadata, &corrupted);

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
    assert!(!daemon.spool.has_object(object_id));
    cleanup(&root);
}

#[tokio::test]
async fn invalid_metadata_is_rejected() {
    let (daemon, root) = start_daemon("invalid-metadata-is-rejected").await;
    let mut fixture = native_fixture();
    let object_id = fixture.metadata["object_id"].as_str().unwrap().to_string();
    fixture.metadata["schema_version"] = Value::String("bifrost.kv_object.future".to_string());
    fixture.metadata_bytes = serde_json::to_vec(&fixture.metadata).unwrap();
    let manifest = manifest_for(&fixture.metadata, &fixture.payload);

    let outcome = put_validated_object(
        &daemon.endpoint,
        fixture.metadata_bytes,
        fixture.payload,
        manifest,
    )
    .await
    .unwrap();

    assert!(!outcome.accepted);
    assert_eq!(outcome.reason, "unknown_schema_version");
    assert!(!daemon.spool.has_object(&object_id));
    cleanup(&root);
}

#[tokio::test]
async fn object_id_mismatch_is_rejected() {
    let (daemon, root) = start_daemon("object-id-mismatch-is-rejected").await;
    let mut fixture = native_fixture();
    let original_object_id = fixture.metadata["object_id"].as_str().unwrap().to_string();
    let wrong_object_id = flip_last_hex_char(&original_object_id);
    fixture.metadata["object_id"] = Value::String(wrong_object_id);
    fixture.metadata_bytes = serde_json::to_vec(&fixture.metadata).unwrap();
    let manifest = manifest_for(&fixture.metadata, &fixture.payload);

    let outcome = put_validated_object(
        &daemon.endpoint,
        fixture.metadata_bytes,
        fixture.payload,
        manifest,
    )
    .await
    .unwrap();

    assert!(!outcome.accepted);
    assert_eq!(outcome.reason, "object_id_mismatch");
    assert!(!daemon.spool.has_object(&original_object_id));
    cleanup(&root);
}

#[tokio::test]
async fn partial_transfer_close_before_commit_does_not_commit() {
    let (daemon, root) = start_daemon("partial-transfer-close-before-commit-does-not-commit").await;
    let fixture = native_fixture();
    let object_id = fixture.metadata["object_id"].as_str().unwrap();
    let manifest = manifest_for(&fixture.metadata, &fixture.payload);

    send_begin_then_close(&daemon.endpoint, &fixture.metadata_bytes, &manifest).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    assert!(!daemon.spool.has_object(object_id));
    cleanup(&root);
}

fn flip_last_hex_char(object_id: &str) -> String {
    let mut out = object_id.to_string();
    let last = out.pop().unwrap();
    out.push(if last == '0' { '1' } else { '0' });
    out
}

#[tokio::test]
async fn multiple_sequential_puts_work() {
    let (daemon, root) = start_daemon("multiple-sequential-puts-work").await;
    let fixture = native_fixture();
    let object_id = fixture.metadata["object_id"].as_str().unwrap();

    for _ in 0..3 {
        let outcome = put_object(
            &daemon.endpoint,
            fixture.metadata_bytes.clone(),
            fixture.payload.clone(),
            DEFAULT_CHUNK_SIZE,
            None,
        )
        .await
        .unwrap();
        assert!(outcome.accepted);
    }

    assert!(daemon.spool.has_object(object_id));
    assert_eq!(
        daemon.spool.read_payload(object_id).unwrap(),
        fixture.payload
    );
    cleanup(&root);
}

#[test]
fn xfer_put_help_mentions_endpoint_path_and_chunk_size() {
    let output = Command::new(env!("CARGO_BIN_EXE_bifrost-xfer"))
        .args(["put", "--help"])
        .output()
        .unwrap();

    assert!(output.status.success());
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(stdout.contains("--endpoint"));
    assert!(stdout.contains("--path"));
    assert!(stdout.contains("--chunk-size"));
}

#[test]
fn fixture_still_validates_locally() {
    let fixture = native_fixture();
    let result = validate_object(&fixture.metadata, &fixture.payload, None);
    assert_eq!(result.status, "accepted");
    assert!(fixture.meta_path.exists());
    assert!(fixture.payload_path.exists());
}

async fn send_begin_then_close(endpoint: &str, metadata_bytes: &[u8], manifest: &ChunkManifest) {
    let mut stream = TcpStream::connect(endpoint).await.unwrap();
    let transfer_id = "partial-transfer-001";
    let mut hello = FrameHeader::new(FrameType::Hello, transfer_id, 0);
    hello.peer_role = Some("client".to_string());
    hello.supported_versions = Some(vec!["bifrost.transport.v1alpha1".to_string()]);
    write_frame(&mut stream, &hello, &[]).await.unwrap();
    let response = read_frame(&mut stream).await.unwrap();
    assert_eq!(response.header.frame_type, FrameType::Hello);

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
    begin.flags = Some(BTreeMap::from([(
        "chunk_manifest".to_string(),
        serde_json::to_value(manifest).unwrap(),
    )]));
    write_frame(&mut stream, &begin, metadata_bytes)
        .await
        .unwrap();
}
