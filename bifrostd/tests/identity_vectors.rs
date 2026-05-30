use bifrostd::cache::compute_object_identity;
use serde::Deserialize;
use serde_json::{Map, Value};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Deserialize)]
struct IdentityVector {
    name: String,
    meta_path: String,
    payload_path: String,
    expected_payload_hash: String,
    expected_descriptor_hash: String,
    expected_object_id: String,
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn read_json(path: &Path) -> Value {
    serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
}

fn read_bytes(path: &Path) -> Vec<u8> {
    fs::read(path).unwrap()
}

fn reverse_keys_recursively(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut keys = map.keys().collect::<Vec<_>>();
            keys.sort_by(|left, right| right.cmp(left));

            let mut reordered = Map::new();
            for key in keys {
                reordered.insert(key.clone(), reverse_keys_recursively(&map[key]));
            }
            Value::Object(reordered)
        }
        Value::Array(items) => Value::Array(items.iter().map(reverse_keys_recursively).collect()),
        _ => value.clone(),
    }
}

#[test]
fn rust_identity_matches_committed_vectors() {
    let root = repo_root();
    let vectors: Vec<IdentityVector> = serde_json::from_value(read_json(
        &root.join("fixtures/test_vectors/object_identity_vectors.json"),
    ))
    .unwrap();
    assert!(!vectors.is_empty());

    for vector in vectors {
        let meta = read_json(&root.join(&vector.meta_path));
        let payload = read_bytes(&root.join(&vector.payload_path));
        let identity = compute_object_identity(&meta, &payload).unwrap();

        assert_eq!(
            identity.payload_hash, vector.expected_payload_hash,
            "{} payload hash",
            vector.name
        );
        assert_eq!(
            identity.descriptor_hash, vector.expected_descriptor_hash,
            "{} descriptor hash",
            vector.name
        );
        assert_eq!(
            identity.object_id, vector.expected_object_id,
            "{} object ID",
            vector.name
        );
    }
}

#[test]
fn rust_object_identity_ignores_recursive_metadata_key_order() {
    let root = repo_root();
    let meta = read_json(&root.join("fixtures/native_valid/tiny_gpt_layer0_block0.meta.json"));
    let payload =
        read_bytes(&root.join("fixtures/native_valid/tiny_gpt_layer0_block0.payload.bin"));

    let original = compute_object_identity(&meta, &payload).unwrap();
    let reordered_meta = reverse_keys_recursively(&meta);
    let reordered = compute_object_identity(&reordered_meta, &payload).unwrap();

    assert_eq!(original, reordered);
}
