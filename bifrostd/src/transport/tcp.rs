use crate::transport::{
    DecodeLimits, Frame, FrameHeader, TransportError, TransportResult, DEFAULT_MAX_HEADER_LEN,
    DEFAULT_MAX_PAYLOAD_LEN,
};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

pub async fn read_frame(reader: &mut (impl AsyncRead + Unpin)) -> TransportResult<Frame> {
    read_frame_with_limits(reader, DecodeLimits::default()).await
}

pub async fn read_frame_with_limits(
    reader: &mut (impl AsyncRead + Unpin),
    limits: DecodeLimits,
) -> TransportResult<Frame> {
    let mut header_len_bytes = [0_u8; 4];
    reader.read_exact(&mut header_len_bytes).await?;
    let header_len = u32::from_be_bytes(header_len_bytes) as usize;
    if header_len > limits.max_header_len {
        return Err(TransportError::HeaderTooLarge {
            actual: header_len,
            max: limits.max_header_len,
        });
    }

    let mut header_bytes = vec![0_u8; header_len];
    reader.read_exact(&mut header_bytes).await?;
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
        match reader.read(&mut payload[read_len..]).await {
            Ok(0) => {
                return Err(TransportError::PayloadLengthMismatch {
                    expected: header.payload_len,
                    actual: read_len as u64,
                });
            }
            Ok(n) => read_len += n,
            Err(err) if err.kind() == std::io::ErrorKind::Interrupted => {}
            Err(err) if err.kind() == std::io::ErrorKind::UnexpectedEof => {
                return Err(TransportError::PayloadLengthMismatch {
                    expected: header.payload_len,
                    actual: read_len as u64,
                });
            }
            Err(err) => return Err(TransportError::Io(err)),
        }
    }
    header.validate(payload.len() as u64)?;
    Ok(Frame { header, payload })
}

pub async fn write_frame(
    writer: &mut (impl AsyncWrite + Unpin),
    header: &FrameHeader,
    payload: &[u8],
) -> TransportResult<()> {
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

    writer
        .write_all(&(header_bytes.len() as u32).to_be_bytes())
        .await?;
    writer.write_all(&header_bytes).await?;
    writer.write_all(payload).await?;
    writer.flush().await?;
    Ok(())
}
