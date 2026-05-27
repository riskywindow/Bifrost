# Phase 2 Protocol

Last verified: 2026-05-27

## Version

The Phase 2 transport protocol version string is:

```text
bifrost.transport.v1alpha1
```

Peers must reject unsupported protocol versions with an `error` frame.

## Frame format

Each frame is encoded as:

```text
u32 header_len
header_len bytes of UTF-8 JSON header
remaining frame payload bytes, if any
```

`header_len` is an unsigned 32-bit integer in network byte order.

The JSON header must be canonical enough for parsing and validation, but the
transport header is not part of Phase 1 KV object identity. Frame headers must
not include mutable spool state inside KV descriptors.

The header must include:

```text
type: string
version: "bifrost.transport.v1alpha1"
request_id: string
body_len: integer
```

The raw payload bytes immediately follow the JSON header. The number of raw
payload bytes is declared by `body_len`. Frames without raw payload bytes must
set `body_len` to `0`.

Receivers must reject:

1. Malformed `header_len`.
2. Non-UTF-8 or non-JSON headers.
3. Unsupported protocol versions.
4. Unknown frame types.
5. Missing required fields.
6. Payload byte count mismatches.
7. Frames that are invalid for the current transfer state.

## Frame types

### hello

Starts protocol negotiation.

Required fields:

```text
type: "hello"
version: "bifrost.transport.v1alpha1"
request_id: string
body_len: 0
peer_role: "client" | "daemon"
supported_versions: array of strings
```

### put_begin

Starts an object upload.

Required fields:

```text
type: "put_begin"
version: "bifrost.transport.v1alpha1"
request_id: string
body_len: integer
object_id: string
descriptor_len: integer
payload_len: integer
chunk_size: integer
chunk_count: integer
target_profile_id: string
```

The raw payload bytes for this frame contain the Phase 1 descriptor JSON bytes.
`body_len` must equal `descriptor_len`.

### chunk

Transfers one payload chunk.

Required fields:

```text
type: "chunk"
version: "bifrost.transport.v1alpha1"
request_id: string
body_len: integer
object_id: string
chunk_index: integer
chunk_offset: integer
payload_len: integer
chunk_hash: string
```

The raw payload bytes contain exactly one payload chunk. `chunk_hash` is a
transport-level hash of the chunk bytes and is used to reject corrupted chunks
early. Whole-object Phase 1 validation is still required before commit.
`body_len` must equal `payload_len`.

### chunk_ack

Acknowledges one accepted chunk.

Required fields:

```text
type: "chunk_ack"
version: "bifrost.transport.v1alpha1"
request_id: string
body_len: 0
object_id: string
chunk_index: integer
status: "accepted" | "duplicate" | "rejected"
reason: string
```

`reason` may be empty only when `status` is `accepted`.

### put_commit

Requests validation and atomic commit after all chunks have been sent.

Required fields:

```text
type: "put_commit"
version: "bifrost.transport.v1alpha1"
request_id: string
body_len: 0
object_id: string
chunk_count: integer
payload_len: integer
```

### put_result

Reports final PUT status.

Required fields:

```text
type: "put_result"
version: "bifrost.transport.v1alpha1"
request_id: string
body_len: 0
object_id: string
status: "committed" | "rejected"
reason: string
```

`reason` may be empty only when `status` is `committed`.

### get_begin

Requests a committed object.

Required fields:

```text
type: "get_begin"
version: "bifrost.transport.v1alpha1"
request_id: string
body_len: 0
object_id: string
```

### get_result

Returns GET status and descriptor metadata.

Required fields:

```text
type: "get_result"
version: "bifrost.transport.v1alpha1"
request_id: string
body_len: integer
object_id: string
status: "found" | "miss" | "rejected"
reason: string
descriptor_len: integer
payload_len: integer
chunk_size: integer
chunk_count: integer
```

For `status: "found"`, the raw payload bytes contain the committed descriptor
JSON bytes and `body_len` must equal `descriptor_len`. Payload bytes are then
sent with `chunk` frames. For any other status, `body_len` must be `0`.

### has_request

Checks whether an object is committed and servable.

Required fields:

```text
type: "has_request"
version: "bifrost.transport.v1alpha1"
request_id: string
body_len: 0
object_id: string
```

### has_result

Reports whether an object is committed and servable.

Required fields:

```text
type: "has_result"
version: "bifrost.transport.v1alpha1"
request_id: string
body_len: 0
object_id: string
present: boolean
reason: string
```

`present` must be false for staged, partial, corrupt, rejected, or unknown
objects.

### ping

Liveness check.

Required fields:

```text
type: "ping"
version: "bifrost.transport.v1alpha1"
request_id: string
body_len: 0
```

### pong

Liveness response.

Required fields:

```text
type: "pong"
version: "bifrost.transport.v1alpha1"
request_id: string
body_len: 0
```

### error

Reports a protocol or transfer error.

Required fields:

```text
type: "error"
version: "bifrost.transport.v1alpha1"
request_id: string
body_len: 0
code: string
message: string
fatal: boolean
```

`message` is for diagnostics. Tests should assert stable `code` values rather
than full messages.

## PUT lifecycle

1. Client and daemon exchange `hello`.
2. Client sends `put_begin` with descriptor bytes.
3. Daemon validates the transfer shape and creates a staging record.
4. Client sends `chunk` frames.
5. Daemon verifies each chunk hash and replies with `chunk_ack`.
6. Client sends `put_commit`.
7. Daemon verifies all chunks are present.
8. Daemon reassembles the payload.
9. Daemon runs Phase 1 Rust validation on descriptor, payload, and target
   profile.
10. Daemon atomically commits the object only if validation passes.
11. Daemon sends `put_result`.

If any required chunk is missing or whole-object validation fails, the result is
`status: "rejected"` and the object remains uncommitted.

## GET lifecycle

1. Client and daemon exchange `hello`.
2. Client sends `get_begin`.
3. Daemon checks only committed objects.
4. If absent, staged, partial, corrupt, or invalid, daemon sends
   `get_result` with `status: "miss"` or `status: "rejected"`.
5. If present, daemon sends `get_result` with descriptor bytes.
6. Daemon sends committed payload bytes as `chunk` frames.
7. Client verifies chunk hashes and full Phase 1 identity before using the
   object.

The daemon must never serve from staging.

## Error handling rules

Protocol errors use `error` frames. Object validation failures use `put_result`,
`get_result`, or `has_result` with stable reasons.

Receivers must fail closed:

1. Unknown frame type: fatal protocol error.
2. Unsupported version: fatal protocol error.
3. Missing required field: fatal protocol error.
4. Invalid transfer state: fatal protocol error for that request.
5. Chunk hash mismatch: reject that chunk and allow retry while the request is
   live.
6. Missing chunk at commit: reject the PUT.
7. Phase 1 validation failure: reject the PUT with the Phase 1 reason code.
8. Staged object requested by GET or HAS: report miss.

Transport retries must not create multiple committed identities for the same
object. Duplicate chunks are allowed only when they match the already accepted
chunk bytes and metadata.
