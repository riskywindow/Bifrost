use bifrostd::cache::{compute_object_identity, compute_payload_hash};
use bifrostd::store::{EvictionPolicy, EvictionRequest, MemoryTierConfig, Store};
use bifrostd::transport::{chunk_bytes, iter_chunks, DEFAULT_CHUNK_SIZE};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

struct Fixture {
    metadata_bytes: Vec<u8>,
    metadata: Value,
    payload: Vec<u8>,
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

fn variant_fixture(seed: u8) -> Fixture {
    let base = native_fixture();
    let mut metadata = base.metadata;
    let mut payload = base.payload;
    payload[0] = payload[0].wrapping_add(seed);
    let payload_hash = compute_payload_hash(&payload);
    metadata["payload_profile"]["byte_length"] = Value::Number((payload.len() as u64).into());
    metadata["integrity"]["chunk_size_bytes"] = Value::Number((payload.len() as u64).into());
    metadata["integrity"]["chunk_hashes"] = Value::Array(vec![Value::String(payload_hash.clone())]);
    metadata["integrity"]["payload_hash"] = Value::String(payload_hash);
    let identity = compute_object_identity(&metadata, &payload).unwrap();
    metadata["object_id"] = Value::String(identity.object_id);
    metadata["integrity"]["payload_hash"] = Value::String(identity.payload_hash);
    metadata["integrity"]["descriptor_hash"] = Value::String(identity.descriptor_hash);
    let metadata_bytes = serde_json::to_vec(&metadata).unwrap();
    Fixture {
        metadata_bytes,
        metadata,
        payload,
    }
}

fn temp_store_root(test_name: &str) -> PathBuf {
    let unique = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    std::env::temp_dir().join(format!(
        "bifrostd-store-memory-tier-{test_name}-{}-{unique}",
        std::process::id()
    ))
}

fn cleanup(path: &Path) {
    if path.exists() {
        fs::remove_dir_all(path).unwrap();
    }
}

fn object_id(fixture: &Fixture) -> &str {
    fixture.metadata["object_id"].as_str().unwrap()
}

fn stage_fixture(store: &Store, transfer_id: &str, fixture: &Fixture) {
    let mut manifest = chunk_bytes(&fixture.payload, DEFAULT_CHUNK_SIZE).unwrap();
    manifest.object_id = Some(object_id(fixture).to_string());
    store
        .begin_put(transfer_id, &fixture.metadata_bytes, &manifest)
        .unwrap();
    for chunk in iter_chunks(&fixture.payload, DEFAULT_CHUNK_SIZE).unwrap() {
        store
            .write_chunk(transfer_id, chunk.info.chunk_index, chunk.bytes)
            .unwrap();
    }
}

fn put_fixture(store: &Store, transfer_id: &str, fixture: &Fixture) -> String {
    stage_fixture(store, transfer_id, fixture);
    store.commit_put(transfer_id, None).unwrap().object_id
}

fn memory_store(root: PathBuf, capacity_bytes: u64, cache_payloads: bool) -> Store {
    Store::open_with_memory_tier(
        root,
        MemoryTierConfig {
            capacity_bytes,
            cache_payloads,
            max_object_bytes: None,
        },
    )
    .unwrap()
}

#[test]
fn memory_tier_is_disabled_by_default() {
    let root = temp_store_root("disabled-default");
    let store = Store::open(root.clone()).unwrap();
    let fixture = native_fixture();
    let object_id = put_fixture(&store, "transfer-001", &fixture);

    assert_eq!(
        store.get_metadata(&object_id).unwrap(),
        fixture.metadata_bytes
    );
    let stats = store.stats().unwrap();
    assert!(!stats.memory_tier_enabled);
    assert_eq!(stats.memory_tier_capacity_bytes, 0);
    assert_eq!(stats.memory_tier_hits, 0);
    assert_eq!(stats.memory_tier_misses, 0);
    cleanup(&root);
}

#[test]
fn metadata_cache_hit_increments_hits() {
    let root = temp_store_root("metadata-hit");
    let fixture = native_fixture();
    let store = memory_store(
        root.clone(),
        (fixture.metadata_bytes.len() * 2) as u64,
        false,
    );
    let object_id = put_fixture(&store, "transfer-001", &fixture);

    assert_eq!(
        store.get_metadata(&object_id).unwrap(),
        fixture.metadata_bytes
    );
    assert_eq!(
        store.get_metadata(&object_id).unwrap(),
        fixture.metadata_bytes
    );

    let stats = store.stats().unwrap();
    assert!(stats.memory_tier_enabled);
    assert_eq!(stats.memory_tier_hits, 1);
    assert_eq!(stats.memory_tier_misses, 1);
    assert_eq!(stats.memory_tier_bytes, fixture.metadata_bytes.len() as i64);
    cleanup(&root);
}

#[test]
fn payload_cache_hit_returns_exact_bytes() {
    let root = temp_store_root("payload-hit");
    let fixture = native_fixture();
    let capacity = (fixture.metadata_bytes.len() + fixture.payload.len()) as u64 * 2;
    let store = memory_store(root.clone(), capacity, true);
    let object_id = put_fixture(&store, "transfer-001", &fixture);

    assert_eq!(store.get_payload(&object_id).unwrap(), fixture.payload);
    assert_eq!(store.get_payload(&object_id).unwrap(), fixture.payload);

    let stats = store.stats().unwrap();
    assert_eq!(stats.memory_tier_hits, 1);
    assert_eq!(stats.memory_tier_misses, 1);
    cleanup(&root);
}

#[test]
fn memory_tier_respects_capacity() {
    let root = temp_store_root("capacity");
    let fixture = native_fixture();
    let store = memory_store(root.clone(), fixture.metadata_bytes.len() as u64 - 1, false);
    let object_id = put_fixture(&store, "transfer-001", &fixture);

    assert_eq!(
        store.get_metadata(&object_id).unwrap(),
        fixture.metadata_bytes
    );
    assert_eq!(
        store.get_metadata(&object_id).unwrap(),
        fixture.metadata_bytes
    );

    let stats = store.stats().unwrap();
    assert_eq!(stats.memory_tier_bytes, 0);
    assert_eq!(stats.memory_tier_hits, 0);
    assert_eq!(stats.memory_tier_misses, 2);
    cleanup(&root);
}

#[test]
fn memory_tier_evicts_lru() {
    let root = temp_store_root("lru");
    let first = variant_fixture(1);
    let second = variant_fixture(2);
    let third = variant_fixture(3);
    let sizes = [
        first.metadata_bytes.len() as u64,
        second.metadata_bytes.len() as u64,
        third.metadata_bytes.len() as u64,
    ];
    let mut pair_capacities = [
        sizes[0] + sizes[1],
        sizes[0] + sizes[2],
        sizes[1] + sizes[2],
    ];
    pair_capacities.sort_unstable();
    let store = memory_store(root.clone(), pair_capacities[2], false);
    let first_id = put_fixture(&store, "transfer-001", &first);
    let second_id = put_fixture(&store, "transfer-002", &second);
    let third_id = put_fixture(&store, "transfer-003", &third);

    store.get_metadata(&first_id).unwrap();
    store.get_metadata(&second_id).unwrap();
    store.get_metadata(&first_id).unwrap();
    store.get_metadata(&third_id).unwrap();

    let before = store.stats().unwrap();
    assert_eq!(before.memory_tier_evictions, 1);
    store.get_metadata(&first_id).unwrap();
    store.get_metadata(&second_id).unwrap();
    let after = store.stats().unwrap();
    assert_eq!(after.memory_tier_hits, before.memory_tier_hits + 1);
    assert_eq!(after.memory_tier_misses, before.memory_tier_misses + 1);
    cleanup(&root);
}

#[test]
fn quarantined_object_is_invalidated() {
    let root = temp_store_root("quarantine-invalidates");
    let fixture = native_fixture();
    let capacity = (fixture.metadata_bytes.len() + fixture.payload.len()) as u64 * 2;
    let store = memory_store(root.clone(), capacity, true);
    let object_id = put_fixture(&store, "transfer-001", &fixture);

    store.get_payload(&object_id).unwrap();
    assert!(store.stats().unwrap().memory_tier_bytes > 0);

    store.mark_quarantined(&object_id, "test").unwrap();

    let stats = store.stats().unwrap();
    assert_eq!(stats.memory_tier_bytes, 0);
    assert!(store.get_payload(&object_id).is_err());
    cleanup(&root);
}

#[test]
fn evicted_object_is_invalidated() {
    let root = temp_store_root("eviction-invalidates");
    let fixture = native_fixture();
    let capacity = (fixture.metadata_bytes.len() + fixture.payload.len()) as u64 * 2;
    let store = memory_store(root.clone(), capacity, true);
    let object_id = put_fixture(&store, "transfer-001", &fixture);

    store.get_payload(&object_id).unwrap();
    assert!(store.stats().unwrap().memory_tier_bytes > 0);

    let report = store
        .evict(EvictionRequest {
            policy: EvictionPolicy::Lru,
            target_bytes: Some(0),
            max_objects: Some(1),
            dry_run: false,
            now_unix_ms: 10_000,
        })
        .unwrap();

    assert_eq!(report.evicted.len(), 1);
    assert_eq!(store.stats().unwrap().memory_tier_bytes, 0);
    assert!(store.get_payload(&object_id).is_err());
    cleanup(&root);
}

#[test]
fn staging_object_is_never_cached() {
    let root = temp_store_root("staging");
    let fixture = native_fixture();
    let store = memory_store(root.clone(), fixture.metadata_bytes.len() as u64 * 2, true);
    stage_fixture(&store, "transfer-001", &fixture);

    assert!(!store.has_object(object_id(&fixture)).unwrap());
    let stats = store.stats().unwrap();
    assert_eq!(stats.memory_tier_bytes, 0);
    assert_eq!(stats.memory_tier_hits, 0);
    assert_eq!(stats.memory_tier_misses, 0);
    cleanup(&root);
}

#[test]
fn reopened_store_starts_with_empty_memory_tier() {
    let root = temp_store_root("restart-empty");
    let fixture = native_fixture();
    let object_id = {
        let store = memory_store(
            root.clone(),
            (fixture.metadata_bytes.len() + fixture.payload.len()) as u64 * 2,
            true,
        );
        let object_id = put_fixture(&store, "transfer-001", &fixture);
        store.get_payload(&object_id).unwrap();
        assert!(store.stats().unwrap().memory_tier_bytes > 0);
        object_id
    };

    let reopened = memory_store(
        root.clone(),
        (fixture.metadata_bytes.len() + fixture.payload.len()) as u64 * 2,
        true,
    );
    let stats = reopened.stats().unwrap();
    assert_eq!(stats.memory_tier_bytes, 0);
    assert_eq!(stats.memory_tier_hits, 0);
    assert_eq!(reopened.get_payload(&object_id).unwrap(), fixture.payload);
    cleanup(&root);
}

#[test]
fn stats_report_memory_tier_hits_and_misses() {
    let root = temp_store_root("stats");
    let fixture = native_fixture();
    let store = memory_store(root.clone(), fixture.metadata_bytes.len() as u64 * 2, false);
    let object_id = put_fixture(&store, "transfer-001", &fixture);

    store.get_metadata(&object_id).unwrap();
    store.get_metadata(&object_id).unwrap();

    let stats = store.stats().unwrap();
    assert!(stats.memory_tier_enabled);
    assert_eq!(
        stats.memory_tier_capacity_bytes,
        fixture.metadata_bytes.len() as i64 * 2
    );
    assert_eq!(stats.memory_tier_hits, 1);
    assert_eq!(stats.memory_tier_misses, 1);
    cleanup(&root);
}
