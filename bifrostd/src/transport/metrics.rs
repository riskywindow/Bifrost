#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct TransportMetrics {
    pub frames_encoded: u64,
    pub frames_decoded: u64,
    pub protocol_errors: u64,
    pub bytes_payload_encoded: u64,
    pub bytes_payload_decoded: u64,
}

impl TransportMetrics {
    pub fn record_encoded(&mut self, payload_len: u64) {
        self.frames_encoded += 1;
        self.bytes_payload_encoded += payload_len;
    }

    pub fn record_decoded(&mut self, payload_len: u64) {
        self.frames_decoded += 1;
        self.bytes_payload_decoded += payload_len;
    }

    pub fn record_protocol_error(&mut self) {
        self.protocol_errors += 1;
    }
}
