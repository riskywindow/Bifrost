use serde::Serialize;
use std::sync::{Arc, Mutex};

#[derive(Debug, Default, Clone, PartialEq, Eq, Serialize)]
pub struct TransportMetricsSnapshot {
    pub frames_encoded: u64,
    pub frames_decoded: u64,
    pub protocol_errors: u64,
    pub bytes_payload_encoded: u64,
    pub bytes_payload_decoded: u64,
    pub transfers_started_total: u64,
    pub transfers_completed_total: u64,
    pub transfers_failed_total: u64,
    pub bytes_sent_total: u64,
    pub bytes_received_total: u64,
    pub chunks_sent_total: u64,
    pub chunks_received_total: u64,
    pub chunks_retried_total: u64,
    pub chunk_timeouts_total: u64,
    pub paths_dead_total: u64,
    pub chunk_ack_latency_ms_p50: Option<u64>,
    pub chunk_ack_latency_ms_p95: Option<u64>,
    pub chunk_ack_latency_ms: Vec<u64>,
    pub validation_failures_total: u64,
    pub commit_failures_total: u64,
}

#[derive(Debug, Default, Clone)]
pub struct TransportMetrics {
    inner: Arc<Mutex<TransportMetricsSnapshot>>,
}

impl TransportMetrics {
    pub fn snapshot(&self) -> TransportMetricsSnapshot {
        let mut snapshot = self.inner.lock().expect("metrics lock poisoned").clone();
        snapshot.chunk_ack_latency_ms.sort_unstable();
        snapshot.chunk_ack_latency_ms_p50 = percentile(&snapshot.chunk_ack_latency_ms, 50);
        snapshot.chunk_ack_latency_ms_p95 = percentile(&snapshot.chunk_ack_latency_ms, 95);
        snapshot
    }

    pub fn record_encoded(&self, payload_len: u64) {
        let mut inner = self.inner.lock().expect("metrics lock poisoned");
        inner.frames_encoded += 1;
        inner.bytes_payload_encoded += payload_len;
    }

    pub fn record_decoded(&self, payload_len: u64) {
        let mut inner = self.inner.lock().expect("metrics lock poisoned");
        inner.frames_decoded += 1;
        inner.bytes_payload_decoded += payload_len;
    }

    pub fn record_protocol_error(&self) {
        self.inner
            .lock()
            .expect("metrics lock poisoned")
            .protocol_errors += 1;
    }

    pub fn record_transfer_started(&self) {
        self.inner
            .lock()
            .expect("metrics lock poisoned")
            .transfers_started_total += 1;
    }

    pub fn record_transfer_completed(&self) {
        self.inner
            .lock()
            .expect("metrics lock poisoned")
            .transfers_completed_total += 1;
    }

    pub fn record_transfer_failed(&self) {
        self.inner
            .lock()
            .expect("metrics lock poisoned")
            .transfers_failed_total += 1;
    }

    pub fn record_bytes_sent(&self, bytes: u64) {
        self.inner
            .lock()
            .expect("metrics lock poisoned")
            .bytes_sent_total += bytes;
    }

    pub fn record_bytes_received(&self, bytes: u64) {
        self.inner
            .lock()
            .expect("metrics lock poisoned")
            .bytes_received_total += bytes;
    }

    pub fn record_chunk_sent(&self, bytes: u64) {
        let mut inner = self.inner.lock().expect("metrics lock poisoned");
        inner.chunks_sent_total += 1;
        inner.bytes_sent_total += bytes;
    }

    pub fn record_chunk_received(&self, bytes: u64) {
        let mut inner = self.inner.lock().expect("metrics lock poisoned");
        inner.chunks_received_total += 1;
        inner.bytes_received_total += bytes;
    }

    pub fn record_chunk_retried(&self) {
        self.inner
            .lock()
            .expect("metrics lock poisoned")
            .chunks_retried_total += 1;
    }

    pub fn record_chunk_timeout(&self) {
        self.inner
            .lock()
            .expect("metrics lock poisoned")
            .chunk_timeouts_total += 1;
    }

    pub fn record_path_dead(&self) {
        self.inner
            .lock()
            .expect("metrics lock poisoned")
            .paths_dead_total += 1;
    }

    pub fn record_chunk_ack_latency_ms(&self, duration_ms: u64) {
        self.inner
            .lock()
            .expect("metrics lock poisoned")
            .chunk_ack_latency_ms
            .push(duration_ms);
    }

    pub fn record_validation_failure(&self) {
        self.inner
            .lock()
            .expect("metrics lock poisoned")
            .validation_failures_total += 1;
    }

    pub fn record_commit_failure(&self) {
        self.inner
            .lock()
            .expect("metrics lock poisoned")
            .commit_failures_total += 1;
    }
}

fn percentile(values: &[u64], percentile: u64) -> Option<u64> {
    if values.is_empty() {
        return None;
    }
    let rank = ((values.len() as u64 * percentile).div_ceil(100)).saturating_sub(1);
    values.get(rank as usize).copied()
}
