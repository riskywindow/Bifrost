use anyhow::{Context, Result};
use bifrostd::cache::validate_object;
use clap::Parser;
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Number, Value};
use std::collections::BTreeSet;
use std::fmt;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

#[derive(Debug, Parser)]
#[command(name = "bifrost-kv-validate")]
#[command(about = "Validate a BIFROST Phase 1 KV object fixture")]
struct Args {
    #[arg(long)]
    meta: PathBuf,
    #[arg(long)]
    payload: PathBuf,
    #[arg(long)]
    target: Option<PathBuf>,
    #[arg(long)]
    json: bool,
}

fn main() -> ExitCode {
    match run() {
        Ok(code) => code,
        Err(error) => {
            eprintln!("bifrost-kv-validate: error: {error:#}");
            ExitCode::from(2)
        }
    }
}

fn run() -> Result<ExitCode> {
    let args = Args::parse();
    let metadata = read_json(&args.meta, "metadata")?;
    let payload = std::fs::read(&args.payload)
        .with_context(|| format!("cannot read payload file {}", args.payload.display()))?;
    let target = args
        .target
        .as_ref()
        .map(|path| read_json(path, "target profile"))
        .transpose()?;

    let result = validate_object(&metadata, &payload, target.as_ref());
    if args.json {
        println!("{}", serde_json::to_string(&result)?);
    } else if result.status == "accepted" {
        println!("ACCEPTED");
    } else {
        println!("REJECTED: {}", result.reason_code);
    }

    Ok(if result.status == "accepted" {
        ExitCode::SUCCESS
    } else {
        ExitCode::from(1)
    })
}

fn read_json(path: &Path, label: &str) -> Result<Value> {
    let bytes = std::fs::read(path)
        .with_context(|| format!("cannot read {label} JSON {}", path.display()))?;
    let value = parse_json_rejecting_duplicate_keys(&bytes)
        .with_context(|| format!("cannot parse {label} JSON {}", path.display()))?;
    Ok(value)
}

fn parse_json_rejecting_duplicate_keys(bytes: &[u8]) -> serde_json::Result<Value> {
    serde_json::from_slice::<NoDuplicateKeysValue>(bytes).map(|value| value.0)
}

struct NoDuplicateKeysValue(Value);

impl<'de> Deserialize<'de> for NoDuplicateKeysValue {
    fn deserialize<D>(deserializer: D) -> std::result::Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        deserializer.deserialize_any(NoDuplicateKeysVisitor)
    }
}

struct NoDuplicateKeysVisitor;

impl<'de> Visitor<'de> for NoDuplicateKeysVisitor {
    type Value = NoDuplicateKeysValue;

    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("any JSON value without duplicate object keys")
    }

    fn visit_bool<E>(self, value: bool) -> std::result::Result<Self::Value, E> {
        Ok(NoDuplicateKeysValue(Value::Bool(value)))
    }

    fn visit_i64<E>(self, value: i64) -> std::result::Result<Self::Value, E> {
        Ok(NoDuplicateKeysValue(Value::Number(Number::from(value))))
    }

    fn visit_u64<E>(self, value: u64) -> std::result::Result<Self::Value, E> {
        Ok(NoDuplicateKeysValue(Value::Number(Number::from(value))))
    }

    fn visit_f64<E>(self, value: f64) -> std::result::Result<Self::Value, E>
    where
        E: de::Error,
    {
        let number = Number::from_f64(value)
            .ok_or_else(|| E::custom("floating-point value is not finite"))?;
        Ok(NoDuplicateKeysValue(Value::Number(number)))
    }

    fn visit_str<E>(self, value: &str) -> std::result::Result<Self::Value, E>
    where
        E: de::Error,
    {
        Ok(NoDuplicateKeysValue(Value::String(value.to_string())))
    }

    fn visit_string<E>(self, value: String) -> std::result::Result<Self::Value, E> {
        Ok(NoDuplicateKeysValue(Value::String(value)))
    }

    fn visit_none<E>(self) -> std::result::Result<Self::Value, E> {
        Ok(NoDuplicateKeysValue(Value::Null))
    }

    fn visit_unit<E>(self) -> std::result::Result<Self::Value, E> {
        Ok(NoDuplicateKeysValue(Value::Null))
    }

    fn visit_seq<A>(self, mut seq: A) -> std::result::Result<Self::Value, A::Error>
    where
        A: SeqAccess<'de>,
    {
        let mut values = Vec::new();
        while let Some(value) = seq.next_element::<NoDuplicateKeysValue>()? {
            values.push(value.0);
        }
        Ok(NoDuplicateKeysValue(Value::Array(values)))
    }

    fn visit_map<A>(self, mut map_access: A) -> std::result::Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut keys = BTreeSet::new();
        let mut map = Map::new();
        while let Some(key) = map_access.next_key::<String>()? {
            if !keys.insert(key.clone()) {
                return Err(de::Error::custom(format!(
                    "duplicate JSON object key: {key}"
                )));
            }
            let value = map_access.next_value::<NoDuplicateKeysValue>()?;
            map.insert(key, value.0);
        }
        Ok(NoDuplicateKeysValue(Value::Object(map)))
    }
}

#[cfg(test)]
mod tests {
    use super::parse_json_rejecting_duplicate_keys;

    #[test]
    fn parse_json_rejects_duplicate_object_keys() {
        let err = parse_json_rejecting_duplicate_keys(
            br#"{"schema_version":"bifrost.kv_object.v1alpha1","schema_version":"future"}"#,
        )
        .unwrap_err();

        assert!(err.to_string().contains("duplicate JSON object key"));
    }

    #[test]
    fn parse_json_rejects_nested_duplicate_object_keys() {
        let err =
            parse_json_rejecting_duplicate_keys(br#"{"outer":{"key":1,"key":2}}"#).unwrap_err();

        assert!(err.to_string().contains("duplicate JSON object key"));
    }
}
