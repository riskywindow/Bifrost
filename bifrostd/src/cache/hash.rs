use serde::{Deserialize, Serialize};
use serde_json::{Map, Number, Value};
use thiserror::Error;

pub const HASH_PREFIX: &str = "blake3:";
pub const OBJECT_ID_PREFIX: &str = "bifrost://object/blake3/";
const OBJECT_ID_DOMAIN: &[u8] = b"bifrost.object_id.v1\0";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ObjectIdentity {
    pub payload_hash: String,
    pub descriptor_hash: String,
    pub object_id: String,
}

#[derive(Debug, Error)]
pub enum HashError {
    #[error("{0}: floats are not supported in canonical JSON")]
    Float(String),
    #[error("{0}: canonical JSON only supports integer numbers")]
    UnsupportedNumber(String),
    #[error("$.integrity: expected object")]
    MissingIntegrity,
    #[error("canonical JSON serialization failed: {0}")]
    Json(#[from] serde_json::Error),
}

pub fn compute_payload_hash(payload: &[u8]) -> String {
    blake3_hex(payload)
}

pub fn normalized_descriptor_for_hashing(
    meta: &Value,
    payload_hash: &str,
) -> Result<Value, HashError> {
    let mut normalized = meta.clone();
    if let Value::Object(root) = &mut normalized {
        root.insert("object_id".to_string(), Value::Null);
        match root.get_mut("integrity") {
            Some(Value::Object(integrity)) => {
                integrity.insert("descriptor_hash".to_string(), Value::Null);
                integrity.insert(
                    "payload_hash".to_string(),
                    Value::String(payload_hash.to_string()),
                );
            }
            _ => return Err(HashError::MissingIntegrity),
        }
    }
    Ok(normalized)
}

pub fn compute_descriptor_hash(meta: &Value, payload_hash: &str) -> Result<String, HashError> {
    let normalized = normalized_descriptor_for_hashing(meta, payload_hash)?;
    Ok(blake3_hex(&canonical_encode(&normalized)?))
}

pub fn compute_object_id(descriptor_hash: &str, payload_hash: &str) -> String {
    let mut material = Vec::new();
    material.extend_from_slice(OBJECT_ID_DOMAIN);
    material.extend_from_slice(descriptor_hash.as_bytes());
    material.push(0);
    material.extend_from_slice(payload_hash.as_bytes());
    format!("{}{}", OBJECT_ID_PREFIX, blake3::hash(&material).to_hex())
}

pub fn compute_object_identity(meta: &Value, payload: &[u8]) -> Result<ObjectIdentity, HashError> {
    let payload_hash = compute_payload_hash(payload);
    let descriptor_hash = compute_descriptor_hash(meta, &payload_hash)?;
    let object_id = compute_object_id(&descriptor_hash, &payload_hash);
    Ok(ObjectIdentity {
        payload_hash,
        descriptor_hash,
        object_id,
    })
}

pub fn canonical_encode(value: &Value) -> Result<Vec<u8>, HashError> {
    validate_canonical_value(value, "$")?;
    let mut out = Vec::new();
    write_canonical(value, &mut out)?;
    Ok(out)
}

fn blake3_hex(data: &[u8]) -> String {
    format!("{}{}", HASH_PREFIX, blake3::hash(data).to_hex())
}

fn validate_canonical_value(value: &Value, path: &str) -> Result<(), HashError> {
    match value {
        Value::Null | Value::Bool(_) | Value::String(_) => Ok(()),
        Value::Number(number) => validate_number(number, path),
        Value::Array(items) => {
            for (index, item) in items.iter().enumerate() {
                validate_canonical_value(item, &format!("{path}[{index}]"))?;
            }
            Ok(())
        }
        Value::Object(map) => {
            for (key, item) in map {
                validate_canonical_value(item, &format!("{path}.{key}"))?;
            }
            Ok(())
        }
    }
}

fn validate_number(number: &Number, path: &str) -> Result<(), HashError> {
    if number.is_f64() {
        return Err(HashError::Float(path.to_string()));
    }
    if number.as_i64().is_none() && number.as_u64().is_none() {
        return Err(HashError::UnsupportedNumber(path.to_string()));
    }
    Ok(())
}

fn write_canonical(value: &Value, out: &mut Vec<u8>) -> Result<(), serde_json::Error> {
    match value {
        Value::Null => out.extend_from_slice(b"null"),
        Value::Bool(true) => out.extend_from_slice(b"true"),
        Value::Bool(false) => out.extend_from_slice(b"false"),
        Value::Number(number) => out.extend_from_slice(number.to_string().as_bytes()),
        Value::String(text) => serde_json::to_writer(out, text)?,
        Value::Array(items) => write_array(items, out)?,
        Value::Object(map) => write_object(map, out)?,
    }
    Ok(())
}

fn write_array(items: &[Value], out: &mut Vec<u8>) -> Result<(), serde_json::Error> {
    out.push(b'[');
    for (index, item) in items.iter().enumerate() {
        if index > 0 {
            out.push(b',');
        }
        write_canonical(item, out)?;
    }
    out.push(b']');
    Ok(())
}

fn write_object(map: &Map<String, Value>, out: &mut Vec<u8>) -> Result<(), serde_json::Error> {
    out.push(b'{');
    let mut keys = map.keys().collect::<Vec<_>>();
    keys.sort();
    for (index, key) in keys.iter().enumerate() {
        if index > 0 {
            out.push(b',');
        }
        serde_json::to_writer(&mut *out, key)?;
        out.push(b':');
        write_canonical(&map[*key], out)?;
    }
    out.push(b'}');
    Ok(())
}
