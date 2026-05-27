# Phase 2 Metrics and Traces

Last verified: 2026-05-27

Phase 2 keeps transport observability local and dependency-light. The daemon and
client can maintain in-memory counters, and both can write newline-delimited JSON
trace events for deterministic test and benchmark inspection.

## In-memory metrics

`bifrostd::transport::TransportMetrics` tracks a shared in-process snapshot with:

1. `transfers_started_total`
2. `transfers_completed_total`
3. `transfers_failed_total`
4. `bytes_sent_total`
5. `bytes_received_total`
6. `chunks_sent_total`
7. `chunks_received_total`
8. `chunks_retried_total`
9. `chunk_ack_latency_ms_p50`
10. `chunk_ack_latency_ms_p95`
11. `chunk_ack_latency_ms`
12. `validation_failures_total`
13. `commit_failures_total`

The frame-level counters from the initial transport work remain available in the
same snapshot. Retry is not implemented yet, so `chunks_retried_total` remains
zero unless a future retry path records it.

## JSONL trace events

Trace files are opt-in:

```text
bifrost-daemon --spool /tmp/bifrost-spool --trace-jsonl /tmp/daemon.jsonl
bifrost-xfer put --meta meta.json --payload payload.bin --trace-jsonl /tmp/put.jsonl
bifrost-xfer get --object-id OBJECT_ID --out out --trace-jsonl /tmp/get.jsonl
```

Each line is one JSON object. Events include:

1. `timestamp_unix_ms`
2. `event_type`
3. `transfer_id`, when applicable
4. `object_id`, when applicable
5. `chunk_index`, when applicable
6. `bytes`, when applicable
7. `path_name`, currently always `primary`
8. `duration_ms`, when applicable
9. `reason_code`, when applicable

The Phase 2 event vocabulary is:

1. `daemon_start`
2. `client_put_begin`
3. `server_put_begin`
4. `chunk_sent`
5. `chunk_received`
6. `chunk_ack`
7. `put_commit_started`
8. `put_commit_accepted`
9. `put_commit_rejected`
10. `get_begin`
11. `get_chunk_sent`
12. `get_completed`
13. `transfer_error`

Rejected PUT commits include a deterministic `reason_code` from the spool or
Phase 1 validator. Transport errors are emitted as `transfer_error` and are not
treated as object validation failures unless the Phase 1 validator rejected the
object.

## CLI JSON output

`bifrost-xfer --json put ...` and `bifrost-xfer --json get ...` print structured
operation output with a metrics snapshot. This is intended for local smoke tests
and ContextStorm-style harnesses, not as a production API.

Prometheus export, dashboards, multipath labels, and ContextStorm reports remain
out of scope for this change.
