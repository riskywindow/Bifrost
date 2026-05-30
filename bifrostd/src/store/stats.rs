#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct StoreStats {
    pub object_count: i64,
    pub total_logical_bytes: i64,
    pub total_bytes_on_disk: i64,
    pub staging_count: i64,
    pub committed_count: i64,
    pub verified_count: i64,
    pub pinned_count: i64,
    pub evictable_count: i64,
    pub evicting_count: i64,
    pub evicted_count: i64,
    pub quarantined_count: i64,
    pub missing_count: i64,
    pub corrupt_count: i64,
    pub total_pin_count: i64,
    pub total_access_count: i64,
}
