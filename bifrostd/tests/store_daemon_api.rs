use bifrostd::spool::Spool;
use bifrostd::transport::{
    chunk_bytes, inspect_store_object, list_opaque_keys, list_store_objects, put_object,
    query_opaque_key, query_store_objects, serve_listener, store_stats, ChunkManifest,
    OpaqueKeyListRequest, OpaqueKeyQueryRequest, StoreObjectFilter, DEFAULT_CHUNK_SIZE,
};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::net::TcpListener;

struct Fixture {
    metadata_bytes: Vec<u8>,
    metadata: Value,
    payload: Vec<u8>,
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

fn fixture(dir: &str, meta: &str, payload: &str) -> Fixture {
    let root = repo_root().join("fixtures").join(dir);
    let meta_path = root.join(meta);
    let payload_path = root.join(payload);
    let metadata_bytes = fs::read(&meta_path).unwrap();
    let metadata = serde_json::from_slice(&metadata_bytes).unwrap();
    let payload = fs::read(&payload_path).unwrap();
    Fixture {
        metadata_bytes,
        metadata,
        payload,
    }
}

fn native_fixture() -> Fixture {
    fixture(
        "native_valid",
        "tiny_gpt_layer0_block0.meta.json",
        "tiny_gpt_layer0_block0.payload.bin",
    )
}

fn opaque_fixture() -> Fixture {
    fixture(
        "opaque_valid",
        "lmcache_blob.meta.json",
        "lmcache_blob.payload.bin",
    )
}

fn object_id(fixture: &Fixture) -> &str {
    fixture.metadata["object_id"].as_str().unwrap()
}

fn prefix_hash(fixture: &Fixture) -> &str {
    fixture.metadata["prefix_profile"]["prefix_hash"]
        .as_str()
        .unwrap()
}

fn opaque_engine_key_hash(fixture: &Fixture) -> &str {
    fixture.metadata["opaque_engine_profile"]["engine_key_hash"]
        .as_str()
        .unwrap()
}

fn engine_name(fixture: &Fixture) -> &str {
    fixture.metadata["engine_profile"]["engine_name"]
        .as_str()
        .unwrap()
}

fn integration_name(fixture: &Fixture) -> &str {
    fixture.metadata["engine_profile"]["integration_name"]
        .as_str()
        .unwrap()
}

fn manifest_for(fixture: &Fixture) -> ChunkManifest {
    let mut manifest = chunk_bytes(&fixture.payload, DEFAULT_CHUNK_SIZE).unwrap();
    manifest.object_id = Some(object_id(fixture).to_string());
    manifest
}

fn temp_root(test_name: &str) -> PathBuf {
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "bifrostd-store-daemon-api-{test_name}-{}-{unique}",
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

async fn put_fixture(endpoint: &str, fixture: &Fixture) -> String {
    let put = put_object(
        endpoint,
        fixture.metadata_bytes.clone(),
        fixture.payload.clone(),
        DEFAULT_CHUNK_SIZE,
        None,
    )
    .await
    .unwrap();
    assert!(put.accepted, "PUT rejected: {}", put.reason);
    put.object_id
}

#[tokio::test]
async fn list_returns_committed_objects_and_skips_staging() {
    let (daemon, root) = start_daemon("list-committed-skips-staging").await;
    let native = native_fixture();
    let object_id = put_fixture(&daemon.endpoint, &native).await;

    let staged = opaque_fixture();
    daemon
        .spool
        .create_staging_transfer(
            "staged-only",
            &staged.metadata_bytes,
            &manifest_for(&staged),
        )
        .unwrap();

    let list = list_store_objects(&daemon.endpoint, StoreObjectFilter::default())
        .await
        .unwrap();

    assert!(list.reason.is_empty());
    assert_eq!(list.objects.len(), 1);
    assert_eq!(list.objects[0].object_id, object_id);
    assert_eq!(list.objects[0].state, "verified");
    cleanup(&root);
}

#[tokio::test]
async fn inspect_returns_metadata_summary() {
    let (daemon, root) = start_daemon("inspect-summary").await;
    let native = native_fixture();
    let object_id = put_fixture(&daemon.endpoint, &native).await;

    let inspect = inspect_store_object(&daemon.endpoint, &object_id)
        .await
        .unwrap();

    assert!(inspect.found);
    let response = inspect.response;
    assert!(response.files_present.unwrap());
    assert!(response.servable.unwrap());
    assert_eq!(response.object.unwrap().object_id, object_id);
    assert_eq!(
        response.descriptor_hash.as_deref(),
        native.metadata["integrity"]["descriptor_hash"].as_str()
    );
    cleanup(&root);
}

#[tokio::test]
async fn query_by_prefix_hash_returns_native_object() {
    let (daemon, root) = start_daemon("query-prefix").await;
    let native = native_fixture();
    let object_id = put_fixture(&daemon.endpoint, &native).await;

    let query = query_store_objects(
        &daemon.endpoint,
        StoreObjectFilter {
            prefix_hash: Some(prefix_hash(&native).to_string()),
            ..StoreObjectFilter::default()
        },
    )
    .await
    .unwrap();

    assert_eq!(query.objects.len(), 1);
    assert_eq!(query.objects[0].object_id, object_id);
    assert_eq!(query.objects[0].object_type, "native_kv_page");
    cleanup(&root);
}

#[tokio::test]
async fn query_by_opaque_engine_key_returns_opaque_object() {
    let (daemon, root) = start_daemon("query-opaque").await;
    let opaque = opaque_fixture();
    let object_id = put_fixture(&daemon.endpoint, &opaque).await;

    let query = query_store_objects(
        &daemon.endpoint,
        StoreObjectFilter {
            opaque_engine_key_hash: Some(opaque_engine_key_hash(&opaque).to_string()),
            ..StoreObjectFilter::default()
        },
    )
    .await
    .unwrap();

    assert_eq!(query.objects.len(), 1);
    assert_eq!(query.objects[0].object_id, object_id);
    assert_eq!(query.objects[0].object_type, "opaque_engine_blob");

    let opaque_query = query_opaque_key(
        &daemon.endpoint,
        OpaqueKeyQueryRequest {
            engine_name: engine_name(&opaque).to_string(),
            integration_name: integration_name(&opaque).to_string(),
            opaque_engine_key_hash: opaque_engine_key_hash(&opaque).to_string(),
        },
    )
    .await
    .unwrap();
    assert!(opaque_query.found);
    assert_eq!(opaque_query.object.unwrap().object_id, object_id);

    let opaque_list = list_opaque_keys(
        &daemon.endpoint,
        OpaqueKeyListRequest {
            engine_name: Some(engine_name(&opaque).to_string()),
            integration_name: Some(integration_name(&opaque).to_string()),
            limit: None,
        },
    )
    .await
    .unwrap();
    assert_eq!(opaque_list.keys.len(), 1);
    assert_eq!(
        opaque_list.keys[0].opaque_engine_key_hash,
        opaque_engine_key_hash(&opaque)
    );
    cleanup(&root);
}

#[tokio::test]
async fn stats_returns_object_count_and_bytes() {
    let (daemon, root) = start_daemon("stats-count-bytes").await;
    let native = native_fixture();
    put_fixture(&daemon.endpoint, &native).await;

    let stats = store_stats(&daemon.endpoint).await.unwrap();

    assert_eq!(stats.stats.object_count, 1);
    assert_eq!(stats.stats.verified_count, 1);
    assert_eq!(stats.stats.total_logical_bytes, native.payload.len() as i64);
    cleanup(&root);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn opaque_cli_list_and_get_key_return_index_entries() {
    let (daemon, root) = start_daemon("opaque-cli").await;
    let opaque = opaque_fixture();
    let object_id = put_fixture(&daemon.endpoint, &opaque).await;

    let list = Command::new(env!("CARGO_BIN_EXE_bifrost-store"))
        .args([
            "opaque",
            "list",
            "--endpoint",
            &daemon.endpoint,
            "--engine-name",
            engine_name(&opaque),
            "--integration-name",
            integration_name(&opaque),
            "--json",
        ])
        .output()
        .unwrap();
    assert!(
        list.status.success(),
        "{}",
        String::from_utf8_lossy(&list.stderr)
    );
    let list_json: Value = serde_json::from_slice(&list.stdout).unwrap();
    assert_eq!(list_json["keys"].as_array().unwrap().len(), 1);
    assert_eq!(list_json["keys"][0]["object_id"], object_id);

    let get_key = Command::new(env!("CARGO_BIN_EXE_bifrost-store"))
        .args([
            "opaque",
            "get-key",
            "--endpoint",
            &daemon.endpoint,
            "--engine-name",
            engine_name(&opaque),
            "--integration-name",
            integration_name(&opaque),
            "--opaque-engine-key-hash",
            opaque_engine_key_hash(&opaque),
            "--json",
        ])
        .output()
        .unwrap();
    assert!(
        get_key.status.success(),
        "{}",
        String::from_utf8_lossy(&get_key.stderr)
    );
    let get_key_json: Value = serde_json::from_slice(&get_key.stdout).unwrap();
    assert_eq!(get_key_json["found"], true);
    assert_eq!(get_key_json["object"]["object_id"], object_id);
    cleanup(&root);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn store_cli_json_output_parses_and_exit_codes_are_stable() {
    let (daemon, root) = start_daemon("cli-json-exit-codes").await;
    let native = native_fixture();
    let object_id = put_fixture(&daemon.endpoint, &native).await;

    let list = Command::new(env!("CARGO_BIN_EXE_bifrost-store"))
        .args(["list", "--endpoint", &daemon.endpoint, "--json"])
        .output()
        .unwrap();
    assert!(
        list.status.success(),
        "{}",
        String::from_utf8_lossy(&list.stderr)
    );
    let list_json: Value = serde_json::from_slice(&list.stdout).unwrap();
    assert_eq!(list_json["objects"].as_array().unwrap().len(), 1);

    let inspect = Command::new(env!("CARGO_BIN_EXE_bifrost-store"))
        .args([
            "inspect",
            "--endpoint",
            &daemon.endpoint,
            "--object-id",
            &object_id,
            "--json",
        ])
        .output()
        .unwrap();
    assert!(inspect.status.success());
    let inspect_json: Value = serde_json::from_slice(&inspect.stdout).unwrap();
    assert_eq!(inspect_json["found"], true);

    let stats = Command::new(env!("CARGO_BIN_EXE_bifrost-store"))
        .args(["stats", "--endpoint", &daemon.endpoint, "--json"])
        .output()
        .unwrap();
    assert!(stats.status.success());
    let stats_json: Value = serde_json::from_slice(&stats.stdout).unwrap();
    assert_eq!(stats_json["object_count"], 1);

    let query_miss = Command::new(env!("CARGO_BIN_EXE_bifrost-store"))
        .args([
            "query",
            "--endpoint",
            &daemon.endpoint,
            "--prefix-hash",
            "blake3:0000000000000000000000000000000000000000000000000000000000000000",
            "--json",
        ])
        .output()
        .unwrap();
    assert_eq!(query_miss.status.code(), Some(1));
    let query_json: Value = serde_json::from_slice(&query_miss.stdout).unwrap();
    assert!(query_json["objects"].as_array().unwrap().is_empty());

    let inspect_miss = Command::new(env!("CARGO_BIN_EXE_bifrost-store"))
        .args([
            "inspect",
            "--endpoint",
            &daemon.endpoint,
            "--object-id",
            "bifrost://object/blake3/0000000000000000000000000000000000000000000000000000000000000000",
            "--json",
        ])
        .output()
        .unwrap();
    assert_eq!(inspect_miss.status.code(), Some(1));

    let io_error = Command::new(env!("CARGO_BIN_EXE_bifrost-store"))
        .args(["stats", "--endpoint", "127.0.0.1:", "--json"])
        .output()
        .unwrap();
    assert_eq!(io_error.status.code(), Some(2));
    cleanup(&root);
}
