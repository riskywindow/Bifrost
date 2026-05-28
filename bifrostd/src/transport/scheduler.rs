use crate::transport::PathSpec;
use std::time::Duration;

const DEGRADED_AFTER_FAILURES: u32 = 2;
const DEGRADED_AFTER_TIMEOUTS: u32 = 1;
const DEAD_AFTER_FAILURES: u32 = 3;
const EWMA_ALPHA: f64 = 0.25;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PathStatus {
    Healthy,
    Degraded,
    Dead,
}

#[derive(Debug, Clone)]
pub struct PathStats {
    pub chunks_sent: u64,
    pub chunks_acked: u64,
    pub chunks_failed: u64,
    pub bytes_sent: u64,
    pub in_flight: u64,
    pub ack_latency_ms: Vec<u64>,
    pub timeout_count: u32,
    pub failures: u32,
    pub ewma_ack_latency_ms: Option<f64>,
    pub approximate_goodput_bytes_per_sec: f64,
    pub status: PathStatus,
}

impl Default for PathStats {
    fn default() -> Self {
        Self {
            chunks_sent: 0,
            chunks_acked: 0,
            chunks_failed: 0,
            bytes_sent: 0,
            in_flight: 0,
            ack_latency_ms: Vec::new(),
            timeout_count: 0,
            failures: 0,
            ewma_ack_latency_ms: None,
            approximate_goodput_bytes_per_sec: 0.0,
            status: PathStatus::Healthy,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ScheduledPath {
    pub spec: PathSpec,
    pub stats: PathStats,
}

#[derive(Debug, Clone)]
pub struct RoundRobinScheduler {
    paths: Vec<ScheduledPath>,
    next_index: usize,
}

impl RoundRobinScheduler {
    pub fn new(paths: Vec<PathSpec>) -> Self {
        Self {
            paths: paths
                .into_iter()
                .map(|spec| ScheduledPath {
                    spec,
                    stats: PathStats::default(),
                })
                .collect(),
            next_index: 0,
        }
    }

    pub fn paths(&self) -> &[ScheduledPath] {
        &self.paths
    }

    pub fn healthy_path_count(&self) -> usize {
        self.paths
            .iter()
            .filter(|path| path.stats.status != PathStatus::Dead)
            .count()
    }

    pub fn select_path(&mut self) -> Option<usize> {
        self.select_path_with_limits(None, u64::MAX)
    }

    pub fn select_path_avoiding(
        &mut self,
        avoid_index: Option<usize>,
        max_inflight_per_path: u64,
    ) -> Option<usize> {
        if let Some(index) = self.select_path_with_limits(avoid_index, max_inflight_per_path) {
            return Some(index);
        }
        self.select_path_with_limits(None, max_inflight_per_path)
    }

    fn select_path_with_limits(
        &mut self,
        avoid_index: Option<usize>,
        max_inflight_per_path: u64,
    ) -> Option<usize> {
        if self.paths.is_empty() || self.healthy_path_count() == 0 {
            return None;
        }

        for preferred_status in [PathStatus::Healthy, PathStatus::Degraded] {
            if let Some(index) =
                self.select_path_matching(avoid_index, max_inflight_per_path, preferred_status)
            {
                return Some(index);
            }
        }
        None
    }

    fn select_path_matching(
        &mut self,
        avoid_index: Option<usize>,
        max_inflight_per_path: u64,
        status: PathStatus,
    ) -> Option<usize> {
        for _ in 0..self.paths.len() {
            let index = self.next_index % self.paths.len();
            self.next_index = (self.next_index + 1) % self.paths.len();
            let stats = &self.paths[index].stats;
            if Some(index) != avoid_index
                && stats.status == status
                && stats.in_flight < max_inflight_per_path
            {
                return Some(index);
            }
        }
        None
    }

    pub fn mark_send_started(&mut self, index: usize, bytes: u64) {
        let path = &mut self.paths[index];
        path.stats.in_flight += 1;
        path.stats.chunks_sent += 1;
        path.stats.bytes_sent += bytes;
    }

    pub fn mark_ack(&mut self, index: usize, latency: Duration) {
        let path = &mut self.paths[index];
        path.stats.in_flight = path.stats.in_flight.saturating_sub(1);
        path.stats.chunks_acked += 1;
        let latency_ms = latency.as_millis() as u64;
        path.stats.ack_latency_ms.push(latency_ms);
        path.stats.ewma_ack_latency_ms = Some(match path.stats.ewma_ack_latency_ms {
            Some(previous) => previous.mul_add(1.0 - EWMA_ALPHA, latency_ms as f64 * EWMA_ALPHA),
            None => latency_ms as f64,
        });
        if latency.as_secs_f64() > 0.0 {
            path.stats.approximate_goodput_bytes_per_sec =
                path.stats.bytes_sent as f64 / latency.as_secs_f64();
        }
        if path.stats.status != PathStatus::Dead {
            path.stats.failures = 0;
            path.stats.status = PathStatus::Healthy;
        }
    }

    pub fn mark_failure(&mut self, index: usize) -> PathStatus {
        let path = &mut self.paths[index];
        path.stats.in_flight = path.stats.in_flight.saturating_sub(1);
        path.stats.chunks_failed += 1;
        path.stats.failures = path.stats.failures.saturating_add(1);
        if path.stats.failures >= DEAD_AFTER_FAILURES {
            path.stats.status = PathStatus::Dead;
        } else if path.stats.failures >= DEGRADED_AFTER_FAILURES {
            path.stats.status = PathStatus::Degraded;
        }
        path.stats.status
    }

    pub fn mark_timeout(&mut self, index: usize) -> PathStatus {
        let path = &mut self.paths[index];
        path.stats.in_flight = path.stats.in_flight.saturating_sub(1);
        path.stats.chunks_failed += 1;
        path.stats.timeout_count = path.stats.timeout_count.saturating_add(1);
        path.stats.failures = path.stats.failures.saturating_add(1);
        if path.stats.failures >= DEAD_AFTER_FAILURES {
            path.stats.status = PathStatus::Dead;
        } else if path.stats.timeout_count >= DEGRADED_AFTER_TIMEOUTS {
            path.stats.status = PathStatus::Degraded;
        }
        path.stats.status
    }

    pub fn mark_dead(&mut self, index: usize) {
        let path = &mut self.paths[index];
        path.stats.in_flight = 0;
        path.stats.status = PathStatus::Dead;
    }

    pub fn path_name(&self, index: usize) -> &str {
        &self.paths[index].spec.name
    }
}
