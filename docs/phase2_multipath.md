# Phase 2 Multipath PUT

Last verified: 2026-05-27

## Scope

Phase 2 multipath is a synthetic local TCP PUT path. It sends chunks for one
validated KV object over multiple TCP connections and lets the daemon
reassemble them into one staging transfer keyed by `transfer_id`.

This is not multipath GET, QUIC, RDMA, compression, parity chunks, adaptive
congestion control, or production cache placement.

## CLI

Single-path PUT continues to use `--endpoint`:

```text
bifrost-xfer put --endpoint 127.0.0.1:7744 --meta meta.json --payload payload.bin
```

Multipath PUT uses one or more `--path NAME=HOST:PORT` entries:

```text
bifrost-xfer put \
  --path p0=127.0.0.1:7744 \
  --path p1=127.0.0.1:7745 \
  --meta meta.json \
  --payload payload.bin
```

When any `--path` entry is present, PUT ignores the simple `--endpoint` value
and uses multipath mode. GET and HAS remain single-path.

## Transfer behavior

The client validates the descriptor and payload locally before opening network
connections. It then:

1. Connects to every configured path.
2. Marks paths that fail connect or handshake as dead.
3. Sends one `put_begin` on the first healthy path.
4. Sends chunks round-robin across non-dead paths.
5. Waits for each `chunk_ack` on the same connection that sent the chunk.
6. Retries a chunk on another path if the selected path fails during send or
   while waiting for the ack.
7. Sends `put_commit` on a remaining healthy path only after every chunk has
   been acknowledged.

If every path is dead, the transfer fails closed.

## Server behavior

All paths use the same `transfer_id`. Daemon listeners that share one `Spool`
instance coordinate staging writes with an in-process staging lock. The spool
still verifies each chunk against the manifest before accepting it.

The daemon accepts duplicate chunks only when the bytes match the previously
accepted chunk. Conflicting duplicate bytes are rejected, and the object is not
committed.

Multipath `put_begin` is marked in frame flags. For those transfers, closing
the connection that sent `put_begin` does not automatically delete staging
state because other paths may still be active. Commit still requires all chunks
to be durably staged and full Phase 1 Rust validation to pass.

## Tracing and Metrics

Client trace events for `chunk_sent`, `chunk_ack`, and path failures include
`path_name`. Server `chunk_received` and `chunk_ack` events use the `path_name`
flag supplied on chunk frames, defaulting to `primary`.

The v1 scheduler records per-path chunks, bytes, in-flight counts, failures,
and ack latency internally. The public metrics snapshot records aggregate
chunks, bytes, ack latency, and retries.

## Current Limits

Multipath GET is not implemented.

The scheduler is intentionally simple: round-robin across non-dead paths with
chunk retry after send or ack failure. It does not optimize for bandwidth,
latency, or congestion.

Staging cleanup for abandoned multipath transfers remains a later retry and
timeout task.
