use bifrostd::cache::{
    compute_object_identity, validate_object, BifrostKvObjectDescriptor, BifrostTargetProfile,
};
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn read_json(path: &Path) -> Value {
    serde_json::from_slice(&fs::read(path).unwrap()).unwrap()
}

fn read_bytes(path: &Path) -> Vec<u8> {
    fs::read(path).unwrap()
}

#[test]
fn parses_required_metadata_structs() {
    let root = repo_root();
    let meta: BifrostKvObjectDescriptor = serde_json::from_value(read_json(
        &root.join("fixtures/native_valid/tiny_gpt_layer0_block0.meta.json"),
    ))
    .unwrap();
    let target: BifrostTargetProfile = serde_json::from_value(read_json(
        &root.join("fixtures/native_valid/target_profile.json"),
    ))
    .unwrap();
    assert_eq!(meta.object_type, "native_kv_page");
    assert_eq!(target.accepts_object_type, "native_kv_page");
}

#[test]
fn native_valid_fixture_is_accepted() {
    let root = repo_root();
    let meta = read_json(&root.join("fixtures/native_valid/tiny_gpt_layer0_block0.meta.json"));
    let payload =
        read_bytes(&root.join("fixtures/native_valid/tiny_gpt_layer0_block0.payload.bin"));
    let target = read_json(&root.join("fixtures/native_valid/target_profile.json"));
    let result = validate_object(&meta, &payload, Some(&target));

    assert_eq!(result.status, "accepted");
    assert_eq!(result.reason_code, "accepted");
    assert_eq!(result.object_id.as_deref(), meta["object_id"].as_str());
    assert_eq!(
        result.payload_hash.as_deref(),
        meta["integrity"]["payload_hash"].as_str()
    );
    assert_eq!(
        result.descriptor_hash.as_deref(),
        meta["integrity"]["descriptor_hash"].as_str()
    );
}

#[test]
fn native_layer3_block7_fixture_is_accepted() {
    let root = repo_root();
    let meta = read_json(
        &root.join("fixtures/native_valid_layer3_block7/tiny_gpt_layer3_block7.meta.json"),
    );
    let payload = read_bytes(
        &root.join("fixtures/native_valid_layer3_block7/tiny_gpt_layer3_block7.payload.bin"),
    );
    let target = read_json(&root.join("fixtures/native_valid_layer3_block7/target_profile.json"));
    let result = validate_object(&meta, &payload, Some(&target));

    assert_eq!(result.status, "accepted");
    assert_eq!(result.reason_code, "accepted");
    assert_eq!(meta["native_tensor_profile"]["layer_id"], 3);
    assert_eq!(meta["native_tensor_profile"]["kv_block_id"], 7);
}

#[test]
fn opaque_valid_fixture_is_accepted() {
    let root = repo_root();
    let meta = read_json(&root.join("fixtures/opaque_valid/lmcache_blob.meta.json"));
    let payload = read_bytes(&root.join("fixtures/opaque_valid/lmcache_blob.payload.bin"));
    let target = read_json(&root.join("fixtures/opaque_valid/target_profile.json"));
    let result = validate_object(&meta, &payload, Some(&target));

    assert_eq!(result.status, "accepted");
    assert_eq!(result.reason_code, "accepted");
    assert_eq!(result.object_id.as_deref(), meta["object_id"].as_str());
}

#[test]
fn invalid_fixtures_match_expected_reason_codes() {
    let root = repo_root();
    let invalid_root = root.join("fixtures/invalid");
    for entry in fs::read_dir(&invalid_root).unwrap() {
        let fixture = entry.unwrap().path();
        if !fixture.is_dir() {
            continue;
        }
        let meta = read_json(&fixture.join("meta.json"));
        let payload = read_bytes(&fixture.join("payload.bin"));
        let target = read_json(&fixture.join("target_profile.json"));
        let expected = read_json(&fixture.join("expected_result.json"));
        let result = validate_object(&meta, &payload, Some(&target));

        assert_eq!(
            result.reason_code,
            expected["reason_code"].as_str().unwrap(),
            "fixture {}",
            fixture.display()
        );
        assert_eq!(
            result.status,
            expected["status"].as_str().unwrap(),
            "fixture {}",
            fixture.display()
        );
        if expected["object_id"].is_string() {
            assert_eq!(result.object_id.as_deref(), expected["object_id"].as_str());
        }
        if expected["payload_hash"].is_string() {
            assert_eq!(
                result.payload_hash.as_deref(),
                expected["payload_hash"].as_str()
            );
        }
        if expected["descriptor_hash"].is_string() {
            assert_eq!(
                result.descriptor_hash.as_deref(),
                expected["descriptor_hash"].as_str()
            );
        }
    }
}

#[test]
fn object_identity_ignores_json_key_order() {
    let root = repo_root();
    let meta = read_json(&root.join("fixtures/native_valid/tiny_gpt_layer0_block0.meta.json"));
    let payload =
        read_bytes(&root.join("fixtures/native_valid/tiny_gpt_layer0_block0.payload.bin"));

    let mut reordered = json!({
        "schema_version": meta["schema_version"].clone(),
        "provenance": meta["provenance"].clone(),
        "prefix_profile": meta["prefix_profile"].clone(),
        "payload_profile": meta["payload_profile"].clone(),
        "opaque_engine_profile": meta["opaque_engine_profile"].clone(),
        "object_type": meta["object_type"].clone(),
        "object_id": meta["object_id"].clone(),
        "native_tensor_profile": meta["native_tensor_profile"].clone(),
        "model_profile": meta["model_profile"].clone(),
        "integrity": meta["integrity"].clone(),
        "engine_profile": meta["engine_profile"].clone(),
        "created_by": meta["created_by"].clone(),
        "created_at_unix_ms": meta["created_at_unix_ms"].clone()
    });
    if let Value::Object(root) = &mut reordered {
        root["model_profile"] = json!({
            "tokenizer_hash": meta["model_profile"]["tokenizer_hash"].clone(),
            "rope_config_hash": meta["model_profile"]["rope_config_hash"].clone(),
            "quantization": meta["model_profile"]["quantization"].clone(),
            "num_layers": meta["model_profile"]["num_layers"].clone(),
            "num_kv_heads": meta["model_profile"]["num_kv_heads"].clone(),
            "num_attention_heads": meta["model_profile"]["num_attention_heads"].clone(),
            "model_revision": meta["model_profile"]["model_revision"].clone(),
            "model_id": meta["model_profile"]["model_id"].clone(),
            "model_hash": meta["model_profile"]["model_hash"].clone(),
            "max_position_embeddings": meta["model_profile"]["max_position_embeddings"].clone(),
            "head_dim": meta["model_profile"]["head_dim"].clone(),
            "dtype": meta["model_profile"]["dtype"].clone(),
            "config_hash": meta["model_profile"]["config_hash"].clone()
        });
    }

    let original = compute_object_identity(&meta, &payload).unwrap();
    let changed_order = compute_object_identity(&reordered, &payload).unwrap();
    assert_eq!(original, changed_order);
}
