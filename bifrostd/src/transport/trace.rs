use serde::Serialize;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, Serialize)]
pub struct TraceEvent {
    pub timestamp_unix_ms: u128,
    pub event_type: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub transfer_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub object_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub chunk_index: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bytes: Option<u64>,
    pub path_name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub duration_ms: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason_code: Option<String>,
}

impl TraceEvent {
    pub fn new(event_type: impl Into<String>) -> Self {
        Self {
            timestamp_unix_ms: now_unix_ms(),
            event_type: event_type.into(),
            transfer_id: None,
            object_id: None,
            chunk_index: None,
            bytes: None,
            path_name: "primary".to_string(),
            duration_ms: None,
            reason_code: None,
        }
    }

    pub fn transfer_id(mut self, transfer_id: impl Into<String>) -> Self {
        self.transfer_id = Some(transfer_id.into());
        self
    }

    pub fn object_id(mut self, object_id: impl Into<String>) -> Self {
        self.object_id = Some(object_id.into());
        self
    }

    pub fn maybe_object_id(mut self, object_id: Option<&str>) -> Self {
        self.object_id = object_id.map(str::to_string);
        self
    }

    pub fn chunk_index(mut self, chunk_index: u64) -> Self {
        self.chunk_index = Some(chunk_index);
        self
    }

    pub fn bytes(mut self, bytes: u64) -> Self {
        self.bytes = Some(bytes);
        self
    }

    pub fn duration_ms(mut self, duration_ms: u64) -> Self {
        self.duration_ms = Some(duration_ms);
        self
    }

    pub fn reason_code(mut self, reason_code: impl Into<String>) -> Self {
        let reason_code = reason_code.into();
        if !reason_code.is_empty() {
            self.reason_code = Some(reason_code);
        }
        self
    }

    pub fn path_name(mut self, path_name: impl Into<String>) -> Self {
        self.path_name = path_name.into();
        self
    }
}

#[derive(Debug, Clone)]
pub struct TraceSink {
    writer: Arc<Mutex<BufWriter<File>>>,
}

impl TraceSink {
    pub fn create(path: impl AsRef<Path>) -> std::io::Result<Self> {
        let file = File::create(path)?;
        Ok(Self {
            writer: Arc::new(Mutex::new(BufWriter::new(file))),
        })
    }

    pub fn emit(&self, event: TraceEvent) -> anyhow::Result<()> {
        let line = serde_json::to_vec(&event)?;
        let mut writer = self.writer.lock().expect("trace lock poisoned");
        writer.write_all(&line)?;
        writer.write_all(b"\n")?;
        writer.flush()?;
        Ok(())
    }
}

fn now_unix_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0)
}
