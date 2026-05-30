use bifrostd::store::Store;
use bifrostd::transport::{get_object, put_object, serve_listener, DEFAULT_CHUNK_SIZE};
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
    store: Store,
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
    let metadata_bytes = fs::read(root.join("tiny_gpt_layer0_block0.meta.json")).unwrap();
    let metadata = serde_json::from_slice(&metadata_bytes).unwrap();
    let payload = fs::read(root.join("tiny_gpt_layer0_block0.payload.bin")).unwrap();
    Fixture {
        metadata_bytes,
        metadata,
        payload,
    }
}

fn temp_root(test_name: &str) -> PathBuf {
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "bifrostd-transport-store-{test_name}-{}-{unique}",
        std::process::id()
    ))
}

async fn start_daemon(test_name: &str) -> (Daemon, PathBuf) {
    let root = temp_root(test_name);
    let store = Store::open(root.clone()).unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = listener.local_addr().unwrap().to_string();
    let server_store = store.clone();
    let task = tokio::spawn(async move {
        let _ = serve_listener(listener, server_store).await;
    });
    (
        Daemon {
            endpoint,
            store,
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

#[tokio::test]
async fn phase2_transport_put_commits_through_store_api() {
    let (daemon, root) = start_daemon("put-through-store").await;
    let fixture = native_fixture();
    let object_id = fixture.metadata["object_id"].as_str().unwrap().to_string();

    let put = put_object(
        &daemon.endpoint,
        fixture.metadata_bytes,
        fixture.payload.clone(),
        DEFAULT_CHUNK_SIZE,
        None,
    )
    .await
    .unwrap();

    assert!(put.accepted);
    assert_eq!(put.object_id, object_id);
    assert!(daemon.store.has_object(&object_id).unwrap());
    assert_eq!(
        daemon.store.get_payload(&object_id).unwrap(),
        fixture.payload
    );
    cleanup(&root);
}

#[tokio::test]
async fn phase2_get_returns_exact_bytes_from_store_api() {
    let (daemon, root) = start_daemon("get-through-store").await;
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
