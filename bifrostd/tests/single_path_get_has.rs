use bifrostd::spool::Spool;
use bifrostd::transport::{
    chunk_bytes, get_object, has_object, iter_chunks, put_object, read_frame, serve_listener,
    write_frame, ChunkManifest, FrameHeader, FrameType, DEFAULT_CHUNK_SIZE,
};
use serde_json::Value;
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::net::TcpListener;

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
        "bifrostd-get-has-{test_name}-{}-{unique}",
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

fn object_id(fixture: &Fixture) -> &str {
    fixture.metadata["object_id"].as_str().unwrap()
}

fn manifest_for(fixture: &Fixture) -> ChunkManifest {
    let mut manifest = chunk_bytes(&fixture.payload, DEFAULT_CHUNK_SIZE).unwrap();
    manifest.object_id = Some(object_id(fixture).to_string());
    manifest
}

#[tokio::test]
async fn put_then_has_returns_exists() {
    let (daemon, root) = start_daemon("put-then-has-returns-exists").await;
    let fixture = native_fixture();

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

    let has = has_object(&daemon.endpoint, &put.object_id).await.unwrap();
    assert!(has.present);
    assert_eq!(has.object_id, put.object_id);
    cleanup(&root);
}

#[tokio::test]
async fn has_missing_object_returns_not_exists() {
    let (daemon, root) = start_daemon("has-missing-object-returns-not-exists").await;

    let has = has_object(
        &daemon.endpoint,
        "bifrost://object/blake3/0000000000000000000000000000000000000000000000000000000000000000",
    )
    .await
    .unwrap();

    assert!(!has.present);
    assert_eq!(has.reason, "not_found");
    cleanup(&root);
}

#[tokio::test]
async fn put_then_get_recovers_exact_metadata_and_payload() {
    let (daemon, root) = start_daemon("put-then-get-recovers-exact-metadata-and-payload").await;
    let fixture = native_fixture();
    let expected_metadata = fixture.metadata_bytes.clone();
    let expected_payload = fixture.payload.clone();

    let put = put_object(
        &daemon.endpoint,
        fixture.metadata_bytes,
        fixture.payload,
        32 * 1024,
        None,
    )
    .await
    .unwrap();
    assert!(put.accepted);

    let get = get_object(&daemon.endpoint, &put.object_id, 32 * 1024)
        .await
        .unwrap();
    assert!(get.found, "GET failed: {}", get.reason);
    assert_eq!(get.metadata_bytes, expected_metadata);
    assert_eq!(get.payload, expected_payload);
    cleanup(&root);
}

#[tokio::test]
async fn get_missing_object_returns_failure() {
    let (daemon, root) = start_daemon("get-missing-object-returns-failure").await;

    let get = get_object(
        &daemon.endpoint,
        "bifrost://object/blake3/0000000000000000000000000000000000000000000000000000000000000000",
        DEFAULT_CHUNK_SIZE,
    )
    .await
    .unwrap();

    assert!(!get.found);
    assert_eq!(get.reason, "not_found");
    cleanup(&root);
}

#[tokio::test]
async fn get_never_serves_staged_partial_object() {
    let (daemon, root) = start_daemon("get-never-serves-staged-partial-object").await;
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();
    let manifest = manifest_for(&fixture);
    daemon
        .spool
        .create_staging_transfer("staged-only", &fixture.metadata_bytes, &manifest)
        .unwrap();

    let has = has_object(&daemon.endpoint, &object_id).await.unwrap();
    let get = get_object(&daemon.endpoint, &object_id, DEFAULT_CHUNK_SIZE)
        .await
        .unwrap();

    assert!(!has.present);
    assert!(!get.found);
    assert_eq!(get.reason, "not_found");
    cleanup(&root);
}

#[tokio::test]
async fn get_detects_corrupted_transfer_chunk_in_client_reassembly() {
    let fixture = native_fixture();
    let object_id = object_id(&fixture).to_string();
    let endpoint = start_corrupt_get_server(fixture).await;

    let get = get_object(&endpoint, &object_id, DEFAULT_CHUNK_SIZE)
        .await
        .unwrap();

    assert!(!get.found);
    assert!(get.reason.contains("hash mismatch"), "{}", get.reason);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn xfer_has_and_get_cli_smoke() {
    let (daemon, root) = start_daemon("xfer-has-and-get-cli-smoke").await;
    let fixture = native_fixture();
    let out_dir = root.join("download");

    let put = Command::new(env!("CARGO_BIN_EXE_bifrost-xfer"))
        .args([
            "put",
            "--endpoint",
            &daemon.endpoint,
            "--meta",
            fixture.meta_path.to_str().unwrap(),
            "--payload",
            fixture.payload_path.to_str().unwrap(),
        ])
        .output()
        .unwrap();
    assert!(
        put.status.success(),
        "{}",
        String::from_utf8_lossy(&put.stderr)
    );

    let object_id = object_id(&fixture);
    let has = Command::new(env!("CARGO_BIN_EXE_bifrost-xfer"))
        .args([
            "has",
            "--endpoint",
            &daemon.endpoint,
            "--object-id",
            object_id,
        ])
        .output()
        .unwrap();
    assert!(
        has.status.success(),
        "{}",
        String::from_utf8_lossy(&has.stderr)
    );
    assert_eq!(String::from_utf8(has.stdout).unwrap().trim(), "yes");

    let get = Command::new(env!("CARGO_BIN_EXE_bifrost-xfer"))
        .args([
            "get",
            "--endpoint",
            &daemon.endpoint,
            "--object-id",
            object_id,
            "--out",
            out_dir.to_str().unwrap(),
        ])
        .output()
        .unwrap();
    assert!(
        get.status.success(),
        "{}",
        String::from_utf8_lossy(&get.stderr)
    );
    assert_eq!(
        fs::read(out_dir.join("meta.json")).unwrap(),
        fixture.metadata_bytes
    );
    assert_eq!(
        fs::read(out_dir.join("payload.bin")).unwrap(),
        fixture.payload
    );
    cleanup(&root);
}

async fn start_corrupt_get_server(fixture: Fixture) -> String {
    let object_id = object_id(&fixture).to_string();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = listener.local_addr().unwrap().to_string();
    tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let hello = read_frame(&mut stream).await.unwrap();
        assert_eq!(hello.header.frame_type, FrameType::Hello);
        let mut daemon_hello =
            FrameHeader::new(FrameType::Hello, hello.header.transfer_id.clone(), 0);
        daemon_hello.peer_role = Some("daemon".to_string());
        write_frame(&mut stream, &daemon_hello, &[]).await.unwrap();

        let request = read_frame(&mut stream).await.unwrap();
        assert_eq!(request.header.frame_type, FrameType::GetBegin);
        let mut manifest = chunk_bytes(&fixture.payload, DEFAULT_CHUNK_SIZE).unwrap();
        manifest.object_id = Some(object_id.clone());

        let mut result = FrameHeader::new(
            FrameType::GetResult,
            request.header.transfer_id.clone(),
            fixture.metadata_bytes.len() as u64,
        );
        result.object_id = Some(object_id.clone());
        result.status = Some("found".to_string());
        result.reason = Some(String::new());
        result.descriptor_len = Some(fixture.metadata_bytes.len() as u64);
        result.object_payload_len = Some(manifest.payload_len);
        result.chunk_size = Some(manifest.chunk_size as u64);
        result.total_chunks = Some(manifest.total_chunks);
        result.payload_hash = Some(manifest.payload_hash.clone());
        result.flags = Some(BTreeMap::from([(
            "chunk_manifest".to_string(),
            serde_json::to_value(&manifest).unwrap(),
        )]));
        write_frame(&mut stream, &result, &fixture.metadata_bytes)
            .await
            .unwrap();

        let chunks = iter_chunks(&fixture.payload, DEFAULT_CHUNK_SIZE).unwrap();
        let first = &chunks[0];
        let mut corrupted = first.bytes.to_vec();
        corrupted[0] ^= 0xff;
        let mut chunk_header = FrameHeader::new(
            FrameType::Chunk,
            request.header.transfer_id.clone(),
            corrupted.len() as u64,
        );
        chunk_header.object_id = Some(object_id);
        chunk_header.chunk_index = Some(first.info.chunk_index);
        chunk_header.total_chunks = Some(manifest.total_chunks);
        chunk_header.chunk_offset = Some(first.info.offset);
        chunk_header.object_payload_len = Some(first.info.len);
        chunk_header.payload_hash = Some(first.info.hash.clone());
        write_frame(&mut stream, &chunk_header, &corrupted)
            .await
            .unwrap();
    });
    endpoint
}
