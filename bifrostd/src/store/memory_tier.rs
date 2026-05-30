use crate::store::disk_tier::StoredObject;
use std::collections::{HashMap, VecDeque};
use std::sync::Mutex;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct MemoryTierConfig {
    pub capacity_bytes: u64,
    pub cache_payloads: bool,
    pub max_object_bytes: Option<u64>,
}

impl MemoryTierConfig {
    pub fn disabled() -> Self {
        Self::default()
    }

    pub fn enabled(&self) -> bool {
        self.capacity_bytes > 0
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct MemoryTierStats {
    pub enabled: bool,
    pub bytes: i64,
    pub capacity_bytes: i64,
    pub hits: i64,
    pub misses: i64,
    pub evictions: i64,
}

#[derive(Debug)]
pub struct MemoryTier {
    config: MemoryTierConfig,
    inner: Mutex<MemoryTierInner>,
}

#[derive(Debug, Default)]
struct MemoryTierInner {
    entries: HashMap<String, MemoryTierEntry>,
    lru: VecDeque<String>,
    bytes: u64,
    hits: i64,
    misses: i64,
    evictions: i64,
}

#[derive(Debug, Clone)]
struct MemoryTierEntry {
    metadata: Vec<u8>,
    payload: Option<Vec<u8>>,
    bytes: u64,
}

impl MemoryTier {
    pub fn new(config: MemoryTierConfig) -> Self {
        Self {
            config,
            inner: Mutex::new(MemoryTierInner::default()),
        }
    }

    pub fn disabled() -> Self {
        Self::new(MemoryTierConfig::disabled())
    }

    pub fn stats(&self) -> MemoryTierStats {
        let inner = self.inner.lock().expect("memory tier lock poisoned");
        MemoryTierStats {
            enabled: self.config.enabled(),
            bytes: inner.bytes as i64,
            capacity_bytes: self.config.capacity_bytes as i64,
            hits: inner.hits,
            misses: inner.misses,
            evictions: inner.evictions,
        }
    }

    pub fn get_metadata(&self, object_id: &str) -> Option<Vec<u8>> {
        if !self.config.enabled() {
            return None;
        }
        let mut inner = self.inner.lock().expect("memory tier lock poisoned");
        let metadata = inner
            .entries
            .get(object_id)
            .map(|entry| entry.metadata.clone());
        if metadata.is_some() {
            inner.hits += 1;
            inner.touch(object_id);
        } else {
            inner.misses += 1;
        }
        metadata
    }

    pub fn get_payload(&self, object_id: &str) -> Option<Vec<u8>> {
        if !self.config.enabled() {
            return None;
        }
        let mut inner = self.inner.lock().expect("memory tier lock poisoned");
        let payload = inner
            .entries
            .get(object_id)
            .and_then(|entry| entry.payload.clone());
        if payload.is_some() {
            inner.hits += 1;
            inner.touch(object_id);
        } else {
            inner.misses += 1;
        }
        payload
    }

    pub fn get_object(&self, object_id: &str) -> Option<StoredObject> {
        if !self.config.enabled() {
            return None;
        }
        let mut inner = self.inner.lock().expect("memory tier lock poisoned");
        let object = inner.entries.get(object_id).and_then(|entry| {
            entry.payload.as_ref().map(|payload| StoredObject {
                object_id: object_id.to_string(),
                metadata: entry.metadata.clone(),
                payload: payload.clone(),
            })
        });
        if object.is_some() {
            inner.hits += 1;
            inner.touch(object_id);
        } else {
            inner.misses += 1;
        }
        object
    }

    pub fn insert(&self, object_id: &str, metadata: &[u8], payload: Option<&[u8]>) {
        if !self.config.enabled() {
            return;
        }

        let payload = payload.and_then(|bytes| {
            if self.should_cache_payload(bytes.len() as u64) {
                Some(bytes.to_vec())
            } else {
                None
            }
        });
        let entry_bytes = metadata.len() as u64
            + payload
                .as_ref()
                .map(|payload| payload.len() as u64)
                .unwrap_or(0);
        if entry_bytes == 0 || entry_bytes > self.config.capacity_bytes {
            self.invalidate(object_id);
            return;
        }

        let mut inner = self.inner.lock().expect("memory tier lock poisoned");
        inner.remove_entry(object_id);
        inner.bytes += entry_bytes;
        inner.entries.insert(
            object_id.to_string(),
            MemoryTierEntry {
                metadata: metadata.to_vec(),
                payload,
                bytes: entry_bytes,
            },
        );
        inner.lru.push_back(object_id.to_string());
        inner.evict_to_capacity(self.config.capacity_bytes);
    }

    pub fn invalidate(&self, object_id: &str) {
        if !self.config.enabled() {
            return;
        }
        let mut inner = self.inner.lock().expect("memory tier lock poisoned");
        inner.remove_entry(object_id);
    }

    fn should_cache_payload(&self, payload_bytes: u64) -> bool {
        self.config.cache_payloads
            && self
                .config
                .max_object_bytes
                .map(|max| payload_bytes <= max)
                .unwrap_or(true)
    }
}

impl MemoryTierInner {
    fn touch(&mut self, object_id: &str) {
        self.lru.retain(|entry| entry != object_id);
        self.lru.push_back(object_id.to_string());
    }

    fn remove_entry(&mut self, object_id: &str) -> Option<MemoryTierEntry> {
        self.lru.retain(|entry| entry != object_id);
        let removed = self.entries.remove(object_id);
        if let Some(entry) = &removed {
            self.bytes = self.bytes.saturating_sub(entry.bytes);
        }
        removed
    }

    fn evict_to_capacity(&mut self, capacity_bytes: u64) {
        while self.bytes > capacity_bytes {
            let Some(object_id) = self.lru.pop_front() else {
                break;
            };
            if let Some(entry) = self.entries.remove(&object_id) {
                self.bytes = self.bytes.saturating_sub(entry.bytes);
                self.evictions += 1;
            }
        }
    }
}
