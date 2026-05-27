use crate::transport::PathSpec;
use std::time::Duration;

const DEGRADED_AFTER_FAILURES: u32 = 2;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PathStatus {
    Healthy,
    Degraded,
    Dead,
}

#[derive(Debug, Clone)]
pub struct PathStats {
    pub chunks_sent: u64,
    pub bytes_sent: u64,
    pub in_flight: u64,
    pub ack_latency_ms: Vec<u64>,
    pub failures: u32,
    pub status: PathStatus,
}

impl Default for PathStats {
    fn default() -> Self {
        Self {
            chunks_sent: 0,
            bytes_sent: 0,
            in_flight: 0,
            ack_latency_ms: Vec::new(),
            failures: 0,
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
        if self.paths.is_empty() || self.healthy_path_count() == 0 {
            return None;
        }

        for _ in 0..self.paths.len() {
            let index = self.next_index % self.paths.len();
            self.next_index = (self.next_index + 1) % self.paths.len();
            if self.paths[index].stats.status != PathStatus::Dead {
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
        path.stats.ack_latency_ms.push(latency.as_millis() as u64);
        if path.stats.status != PathStatus::Dead {
            path.stats.failures = 0;
            path.stats.status = PathStatus::Healthy;
        }
    }

    pub fn mark_failure(&mut self, index: usize) -> PathStatus {
        let path = &mut self.paths[index];
        path.stats.in_flight = path.stats.in_flight.saturating_sub(1);
        path.stats.failures = path.stats.failures.saturating_add(1);
        if path.stats.failures >= DEGRADED_AFTER_FAILURES {
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
