# ContextStorm Summary: small_ci

## Overview

- Operations: 3
- Successes: 3
- Failures: 0
- Bytes sent: 1050876
- Bytes received: 1048576
- Chunks sent: 4
- Retries: 0
- Timeouts: 0

## Per-Run Metrics

| rep | op | success | duration_ms | MiB/s | sent | received | chunks | reason | verified | payload_match |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| 0 | put | True | 139 | 7.210 | 1050876 | 0 | 4 |  | True | True |
| 0 | has | True | 3 | 0.000 | 0 | 0 | 0 |  | True | True |
| 0 | get | True | 84 | 11.905 | 0 | 1048576 | 0 |  | True | True |

## Environment

- Python: 3.12.7
- Platform: macOS-15.6.1-arm64-arm-64bit
- Machine: arm64
- Processor: arm

## Notes

ContextStorm is a local synthetic transport benchmark. It does not run GPU inference, LMCache, vLLM, QUIC, compression, or root-required network emulation.
