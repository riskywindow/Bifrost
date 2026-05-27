use crate::transport::frame::TRANSPORT_VERSION;

pub const PROTOCOL_VERSION: &str = TRANSPORT_VERSION;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PeerRole {
    Client,
    Daemon,
}

impl PeerRole {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Client => "client",
            Self::Daemon => "daemon",
        }
    }
}
