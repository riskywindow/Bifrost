use crate::transport::errors::{TransportError, TransportResult};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::io::{ErrorKind, Read};

pub const TRANSPORT_VERSION: &str = "bifrost.transport.v1alpha1";
pub const DEFAULT_MAX_HEADER_LEN: usize = 64 * 1024;
pub const DEFAULT_MAX_PAYLOAD_LEN: u64 = 16 * 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DecodeLimits {
    pub max_header_len: usize,
    pub max_payload_len: u64,
}

impl Default for DecodeLimits {
    fn default() -> Self {
        Self {
            max_header_len: DEFAULT_MAX_HEADER_LEN,
            max_payload_len: DEFAULT_MAX_PAYLOAD_LEN,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FrameType {
    Hello,
    PutBegin,
    Chunk,
    ChunkAck,
    PutCommit,
    PutResult,
    GetBegin,
    GetResult,
    HasRequest,
    HasResult,
    ListRequest,
    ListResult,
    InspectRequest,
    InspectResult,
    QueryRequest,
    QueryResult,
    StatsRequest,
    StatsResult,
    PinRequest,
    PinResult,
    UnpinRequest,
    UnpinResult,
    TtlRequest,
    TtlResult,
    LifecycleRequest,
    LifecycleResult,
    EvictRequest,
    EvictResult,
    Ping,
    Pong,
    Error,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FrameHeader {
    pub version: String,
    #[serde(rename = "type")]
    pub frame_type: FrameType,
    #[serde(alias = "request_id")]
    pub transfer_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub object_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub chunk_index: Option<u64>,
    #[serde(
        default,
        alias = "chunk_count",
        skip_serializing_if = "Option::is_none"
    )]
    pub total_chunks: Option<u64>,
    #[serde(alias = "body_len")]
    pub payload_len: u64,
    #[serde(default, alias = "chunk_hash", skip_serializing_if = "Option::is_none")]
    pub payload_hash: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub chunk_offset: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub chunk_size: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub descriptor_len: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub object_payload_len: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub present: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub peer_role: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub supported_versions: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub target_profile_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub flags: Option<BTreeMap<String, Value>>,
}

impl FrameHeader {
    pub fn new(frame_type: FrameType, transfer_id: impl Into<String>, payload_len: u64) -> Self {
        Self {
            version: TRANSPORT_VERSION.to_string(),
            frame_type,
            transfer_id: transfer_id.into(),
            object_id: None,
            chunk_index: None,
            total_chunks: None,
            payload_len,
            payload_hash: None,
            chunk_offset: None,
            chunk_size: None,
            descriptor_len: None,
            object_payload_len: None,
            status: None,
            reason: None,
            present: None,
            peer_role: None,
            supported_versions: None,
            target_profile_id: None,
            flags: None,
        }
    }

    pub fn validate(&self, actual_payload_len: u64) -> TransportResult<()> {
        if self.version != TRANSPORT_VERSION {
            return Err(TransportError::UnsupportedVersion(self.version.clone()));
        }
        if self.transfer_id.is_empty() {
            return Err(TransportError::InvalidFrame(
                "transfer_id is required".to_string(),
            ));
        }
        if self.payload_len != actual_payload_len {
            return Err(TransportError::PayloadLengthMismatch {
                expected: self.payload_len,
                actual: actual_payload_len,
            });
        }

        match self.frame_type {
            FrameType::Hello => {
                require_empty_payload(self)?;
                require_non_empty(self.peer_role.as_deref(), "peer_role", self.frame_type)?;
                match self.supported_versions.as_ref() {
                    Some(versions)
                        if versions
                            .iter()
                            .any(|version| version.as_str() == TRANSPORT_VERSION) => {}
                    _ => {
                        return Err(TransportError::InvalidFrame(
                            "Hello requires supported_versions containing current version"
                                .to_string(),
                        ));
                    }
                }
            }
            FrameType::Ping | FrameType::Pong => {
                require_empty_payload(self)?;
            }
            FrameType::Error => {
                require_empty_payload(self)?;
                require_non_empty(self.status.as_deref(), "status", self.frame_type)?;
                require_non_empty(self.reason.as_deref(), "reason", self.frame_type)?;
            }
            FrameType::PutBegin => {
                require_object_id(self)?;
                require_total_chunks(self)?;
                require_descriptor_len_matches_payload(self)?;
                require_object_payload_len(self)?;
                require_chunk_size(self)?;
                require_non_empty(
                    self.target_profile_id.as_deref(),
                    "target_profile_id",
                    self.frame_type,
                )?;
            }
            FrameType::Chunk => {
                require_object_id(self)?;
                require_chunk_index(self)?;
                require_total_chunks(self)?;
                require_payload_hash(self)?;
                require_chunk_offset(self)?;
                require_object_payload_len_matches_payload(self)?;
            }
            FrameType::ChunkAck => {
                require_empty_payload(self)?;
                require_object_id(self)?;
                require_chunk_index(self)?;
                require_non_empty(self.status.as_deref(), "status", self.frame_type)?;
                let status = self.status.as_deref().unwrap_or_default();
                if status != "accepted"
                    && require_non_empty(self.reason.as_deref(), "reason", self.frame_type).is_err()
                {
                    return Err(TransportError::InvalidFrame(
                        "ChunkAck requires reason unless status is accepted".to_string(),
                    ));
                }
            }
            FrameType::PutCommit => {
                require_empty_payload(self)?;
                require_object_id(self)?;
                require_total_chunks(self)?;
                require_object_payload_len(self)?;
            }
            FrameType::PutResult => {
                require_empty_payload(self)?;
                require_object_id(self)?;
                require_non_empty(self.status.as_deref(), "status", self.frame_type)?;
                let status = self.status.as_deref().unwrap_or_default();
                if status != "committed"
                    && require_non_empty(self.reason.as_deref(), "reason", self.frame_type).is_err()
                {
                    return Err(TransportError::InvalidFrame(
                        "PutResult requires reason unless status is committed".to_string(),
                    ));
                }
            }
            FrameType::GetBegin | FrameType::HasRequest => {
                require_empty_payload(self)?;
                require_object_id(self)?;
            }
            FrameType::ListRequest | FrameType::QueryRequest => {}
            FrameType::InspectRequest => {
                require_empty_payload(self)?;
                require_object_id(self)?;
            }
            FrameType::StatsRequest => {
                require_empty_payload(self)?;
            }
            FrameType::PinRequest | FrameType::UnpinRequest => {
                require_empty_payload(self)?;
                require_object_id(self)?;
            }
            FrameType::TtlRequest | FrameType::LifecycleRequest => {
                require_object_id(self)?;
            }
            FrameType::EvictRequest => {}
            FrameType::ListResult
            | FrameType::InspectResult
            | FrameType::QueryResult
            | FrameType::StatsResult
            | FrameType::PinResult
            | FrameType::UnpinResult
            | FrameType::TtlResult
            | FrameType::LifecycleResult
            | FrameType::EvictResult => {
                require_non_empty(self.status.as_deref(), "status", self.frame_type)?;
                let status = self.status.as_deref().unwrap_or_default();
                if status != "ok"
                    && require_non_empty(self.reason.as_deref(), "reason", self.frame_type).is_err()
                {
                    return Err(TransportError::InvalidFrame(format!(
                        "{:?} requires reason unless status is ok",
                        self.frame_type
                    )));
                }
            }
            FrameType::GetResult => {
                require_object_id(self)?;
                require_non_empty(self.status.as_deref(), "status", self.frame_type)?;
                match self.status.as_deref() {
                    Some("found") => {
                        require_descriptor_len_matches_payload(self)?;
                        require_object_payload_len(self)?;
                        require_chunk_size(self)?;
                        require_total_chunks(self)?;
                        require_payload_hash(self)?;
                    }
                    Some("success") => {
                        require_empty_payload(self)?;
                        require_descriptor_len(self)?;
                        require_object_payload_len(self)?;
                        require_chunk_size(self)?;
                        require_total_chunks(self)?;
                        require_payload_hash(self)?;
                    }
                    _ => {
                        require_empty_payload(self)?;
                        require_descriptor_len(self)?;
                        require_object_payload_len_present(self)?;
                        require_chunk_size_present(self)?;
                        require_total_chunks_present(self)?;
                        require_non_empty(self.reason.as_deref(), "reason", self.frame_type)?;
                    }
                }
            }
            FrameType::HasResult => {
                require_empty_payload(self)?;
                require_object_id(self)?;
                require_present(self)?;
            }
        }

        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Frame {
    pub header: FrameHeader,
    pub payload: Vec<u8>,
}

impl Frame {
    pub fn new(header: FrameHeader, payload: Vec<u8>) -> TransportResult<Self> {
        header.validate(payload.len() as u64)?;
        Ok(Self { header, payload })
    }
}

pub fn encode_frame(header: &FrameHeader, payload: &[u8]) -> TransportResult<Vec<u8>> {
    header.validate(payload.len() as u64)?;
    if payload.len() as u64 > DEFAULT_MAX_PAYLOAD_LEN {
        return Err(TransportError::PayloadTooLarge {
            actual: payload.len() as u64,
            max: DEFAULT_MAX_PAYLOAD_LEN,
        });
    }

    let header_bytes = serde_json::to_vec(header)?;
    if header_bytes.len() > DEFAULT_MAX_HEADER_LEN {
        return Err(TransportError::HeaderTooLarge {
            actual: header_bytes.len(),
            max: DEFAULT_MAX_HEADER_LEN,
        });
    }

    let mut out = Vec::with_capacity(4 + header_bytes.len() + payload.len());
    out.extend_from_slice(&(header_bytes.len() as u32).to_be_bytes());
    out.extend_from_slice(&header_bytes);
    out.extend_from_slice(payload);
    Ok(out)
}

pub fn decode_frame(reader: &mut impl Read) -> TransportResult<Frame> {
    decode_frame_with_limits(reader, DecodeLimits::default())
}

pub fn decode_frame_with_limits(
    reader: &mut impl Read,
    limits: DecodeLimits,
) -> TransportResult<Frame> {
    let mut header_len_bytes = [0_u8; 4];
    reader.read_exact(&mut header_len_bytes)?;
    let header_len = u32::from_be_bytes(header_len_bytes) as usize;
    if header_len > limits.max_header_len {
        return Err(TransportError::HeaderTooLarge {
            actual: header_len,
            max: limits.max_header_len,
        });
    }

    let mut header_bytes = vec![0_u8; header_len];
    reader.read_exact(&mut header_bytes)?;
    let header: FrameHeader = serde_json::from_slice(&header_bytes)?;
    if header.payload_len > limits.max_payload_len {
        return Err(TransportError::PayloadTooLarge {
            actual: header.payload_len,
            max: limits.max_payload_len,
        });
    }

    let mut payload = vec![0_u8; header.payload_len as usize];
    let mut read_len = 0_usize;
    while read_len < payload.len() {
        match reader.read(&mut payload[read_len..]) {
            Ok(0) => {
                return Err(TransportError::PayloadLengthMismatch {
                    expected: header.payload_len,
                    actual: read_len as u64,
                });
            }
            Ok(n) => read_len += n,
            Err(err) if err.kind() == ErrorKind::Interrupted => {}
            Err(err) if err.kind() == ErrorKind::UnexpectedEof => {
                return Err(TransportError::PayloadLengthMismatch {
                    expected: header.payload_len,
                    actual: read_len as u64,
                });
            }
            Err(err) => return Err(TransportError::Io(err)),
        }
    }

    if read_len as u64 != header.payload_len {
        return Err(TransportError::PayloadLengthMismatch {
            expected: header.payload_len,
            actual: read_len as u64,
        });
    }

    header.validate(payload.len() as u64)?;
    Ok(Frame { header, payload })
}

fn require_empty_payload(header: &FrameHeader) -> TransportResult<()> {
    if header.payload_len != 0 {
        return Err(TransportError::InvalidFrame(format!(
            "{:?} requires an empty payload",
            header.frame_type
        )));
    }
    Ok(())
}

fn require_object_id(header: &FrameHeader) -> TransportResult<()> {
    match header.object_id.as_deref() {
        Some(value) if !value.is_empty() => Ok(()),
        _ => Err(TransportError::InvalidFrame(format!(
            "{:?} requires object_id",
            header.frame_type
        ))),
    }
}

fn require_chunk_index(header: &FrameHeader) -> TransportResult<()> {
    if header.chunk_index.is_some() {
        Ok(())
    } else {
        Err(TransportError::InvalidFrame(format!(
            "{:?} requires chunk_index",
            header.frame_type
        )))
    }
}

fn require_total_chunks(header: &FrameHeader) -> TransportResult<()> {
    match header.total_chunks {
        Some(value) if value > 0 => Ok(()),
        _ => Err(TransportError::InvalidFrame(format!(
            "{:?} requires total_chunks greater than zero",
            header.frame_type
        ))),
    }
}

fn require_total_chunks_present(header: &FrameHeader) -> TransportResult<()> {
    if header.total_chunks.is_some() {
        Ok(())
    } else {
        Err(TransportError::InvalidFrame(format!(
            "{:?} requires total_chunks",
            header.frame_type
        )))
    }
}

fn require_payload_hash(header: &FrameHeader) -> TransportResult<()> {
    match header.payload_hash.as_deref() {
        Some(value) if !value.is_empty() => Ok(()),
        _ => Err(TransportError::InvalidFrame(format!(
            "{:?} requires payload_hash",
            header.frame_type
        ))),
    }
}

fn require_descriptor_len(header: &FrameHeader) -> TransportResult<()> {
    if header.descriptor_len.is_some() {
        Ok(())
    } else {
        Err(TransportError::InvalidFrame(format!(
            "{:?} requires descriptor_len",
            header.frame_type
        )))
    }
}

fn require_descriptor_len_matches_payload(header: &FrameHeader) -> TransportResult<()> {
    require_descriptor_len(header)?;
    if header.descriptor_len == Some(header.payload_len) {
        Ok(())
    } else {
        Err(TransportError::InvalidFrame(format!(
            "{:?} descriptor_len must match payload length",
            header.frame_type
        )))
    }
}

fn require_object_payload_len(header: &FrameHeader) -> TransportResult<()> {
    match header.object_payload_len {
        Some(value) if value > 0 => Ok(()),
        _ => Err(TransportError::InvalidFrame(format!(
            "{:?} requires payload_len greater than zero",
            header.frame_type
        ))),
    }
}

fn require_object_payload_len_present(header: &FrameHeader) -> TransportResult<()> {
    if header.object_payload_len.is_some() {
        Ok(())
    } else {
        Err(TransportError::InvalidFrame(format!(
            "{:?} requires payload_len",
            header.frame_type
        )))
    }
}

fn require_object_payload_len_matches_payload(header: &FrameHeader) -> TransportResult<()> {
    require_object_payload_len(header)?;
    if header.object_payload_len == Some(header.payload_len) {
        Ok(())
    } else {
        Err(TransportError::InvalidFrame(format!(
            "{:?} payload_len must match body length",
            header.frame_type
        )))
    }
}

fn require_chunk_size(header: &FrameHeader) -> TransportResult<()> {
    match header.chunk_size {
        Some(value) if value > 0 => Ok(()),
        _ => Err(TransportError::InvalidFrame(format!(
            "{:?} requires chunk_size greater than zero",
            header.frame_type
        ))),
    }
}

fn require_chunk_size_present(header: &FrameHeader) -> TransportResult<()> {
    if header.chunk_size.is_some() {
        Ok(())
    } else {
        Err(TransportError::InvalidFrame(format!(
            "{:?} requires chunk_size",
            header.frame_type
        )))
    }
}

fn require_chunk_offset(header: &FrameHeader) -> TransportResult<()> {
    if header.chunk_offset.is_some() {
        Ok(())
    } else {
        Err(TransportError::InvalidFrame(format!(
            "{:?} requires chunk_offset",
            header.frame_type
        )))
    }
}

fn require_present(header: &FrameHeader) -> TransportResult<()> {
    if header.present.is_some() {
        Ok(())
    } else {
        Err(TransportError::InvalidFrame(format!(
            "{:?} requires present",
            header.frame_type
        )))
    }
}

fn require_non_empty(
    value: Option<&str>,
    field_name: &str,
    frame_type: FrameType,
) -> TransportResult<()> {
    match value {
        Some(value) if !value.is_empty() => Ok(()),
        _ => Err(TransportError::InvalidFrame(format!(
            "{:?} requires {field_name}",
            frame_type
        ))),
    }
}
