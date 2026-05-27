use thiserror::Error;

pub type TransportResult<T> = Result<T, TransportError>;

#[derive(Debug, Error)]
pub enum TransportError {
    #[error("unsupported transport protocol version: {0}")]
    UnsupportedVersion(String),

    #[error("invalid frame: {0}")]
    InvalidFrame(String),

    #[error("transport frame header too large: {actual} bytes exceeds {max} bytes")]
    HeaderTooLarge { actual: usize, max: usize },

    #[error("transport frame payload too large: {actual} bytes exceeds {max} bytes")]
    PayloadTooLarge { actual: u64, max: u64 },

    #[error(
        "transport frame payload length mismatch: expected {expected} bytes, got {actual} bytes"
    )]
    PayloadLengthMismatch { expected: u64, actual: u64 },

    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("protocol error: {0}")]
    Protocol(String),
}
