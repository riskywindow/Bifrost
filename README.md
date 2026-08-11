# BIFROST

Correctness-first KV-state transport and storage infrastructure for LLM
serving, built around immutable content-addressed objects and fail-closed
validation.

BIFROST moves opaque or explicitly described KV payloads through a local TCP
transport, validates them in Python and Rust, and makes them visible only after
a durable store commit. The implemented stack includes chunked transfer,
optional multipath retry, a SQLite-backed object catalog, manifests, eviction,
`fsck`, replayable fault scenarios, and an LMCache remote-storage connector.

The project is deliberately strict about its current runtime boundary. The
working serving path is `vLLM -> LMCache -> BIFROST`; the direct vLLM
KVTransfer package is an experimental, fake-vLLM-tested save path. It does not
yet load objects into vLLM, manipulate GPU-resident KV pages, or implement
copy-on-write cache semantics.

## Architecture

```text
vLLM / test harness
        |
        v
     LMCache  -- opaque MemoryObj ownership and cache semantics
        |
        v
lmcache_bifrost connector
        |
        v
 BifrostClient -- PUT / HAS / GET / lookup
        |
        v
    bifrostd   -- validate -> stage -> commit -> catalog
        |
        +-- content-addressed object files
        +-- SQLite catalog and prefix manifests
        +-- JSONL traces, metrics, eviction, and fsck
```

Objects are addressed from canonical metadata and payload hashes. Mutable
transport, catalog, benchmark, and cache-placement state is excluded from
object identity. Staged, partial, corrupt, incompatible, quarantined, or
otherwise uncertain objects cannot satisfy a hit.

## What Is Implemented

| Layer | Implemented evidence |
|---|---|
| Object contract | Canonical descriptors, BLAKE3 identity, deterministic fixtures, stable validation reason codes, and Python/Rust parity tests |
| Transport | Local TCP `PUT`, `HAS`, and `GET`; chunking, reassembly, retry, optional multipath transfer, JSONL traces, and synthetic fault scenarios |
| Durable store | SQLite catalog, compatibility lookup, prefix manifests, pinning, deterministic eviction, quarantine, and `fsck` |
| Model correctness harness | A deterministic CPU tiny transformer with real KV extraction, native page serialization, store-backed rehydration, logit comparison, and cross-process continuation checks |
| LMCache path | Python client, `opaque_engine_blob` codec, LMCache connector adapter, remote connector operations, fake integration tests, and opt-in real-LMCache coverage |
| Serving evaluation | A guarded three-mode vLLM/LMCache matrix with artifact and correctness gates; the recorded single-host run observed connector activity and found BIFROST slower, so no speedup is claimed |
| Direct vLLM path | API inspection, dynamic-import package, deterministic keying/layout commitments, fake-vLLM metadata, and a synchronous CPU-staged save path; load and real-vLLM transfer remain incomplete |

The phase checklists link implementation claims to tests and acceptance gates:
[store](docs/phase3_checklist.md),
[model correctness](docs/phase4_checklist.md),
[LMCache](docs/phase5_checklist.md),
[serving evaluation](docs/phase6_checklist.md), and
[direct vLLM work](docs/phase7_checklist.md).

## Evaluation

ContextStorm runs deterministic local workloads for transport, store,
tiny-model correctness, connector behavior, and serving orchestration. Reports
contain machine-readable inputs, command records, traces, summaries, and
explicit evidence posture.

The Phase 6 matrix compares:

1. `vllm_only`
2. `vllm_lmcache_local_cpu`
3. `vllm_lmcache_bifrost`

The repository's [final Phase 6 review](docs/phase6_checklist.md) records nine
real rows across three repetitions, BIFROST and LMCache activity, a clean
store check, and no performance win. Fake CI serving runs validate the harness
but are never presented as real model-serving evidence.

Run a small CPU-only transport scenario after building the Rust binaries:

```sh
cargo build --manifest-path bifrostd/Cargo.toml --bins
cd contextstorm
PYTHONPATH=. python -m contextstorm.cli run scenarios/small_ci.yaml \
  --runs-root /tmp/contextstorm-runs \
  --run-id local-small
PYTHONPATH=. python -m contextstorm.cli report \
  /tmp/contextstorm-runs/local-small
```

See [ContextStorm](docs/contextstorm.md) and the
[serving benchmark design](docs/phase6_serving_benchmark.md) for scenario and
evidence semantics.

## Quick Start

Install the Python reference package and build the daemon:

```sh
python -m pip install -e "bifrost_py[dev]"
cargo build --manifest-path bifrostd/Cargo.toml --bins
```

Start a local store:

```sh
bifrostd/target/debug/bifrost-daemon \
  --listen 127.0.0.1:7420 \
  --spool /tmp/bifrost-spool \
  --trace-jsonl /tmp/bifrost-daemon.jsonl
```

Transfer and retrieve a committed fixture:

```sh
bifrostd/target/debug/bifrost-xfer put \
  --endpoint 127.0.0.1:7420 \
  --meta fixtures/native_valid/tiny_gpt_layer0_block0.meta.json \
  --payload fixtures/native_valid/tiny_gpt_layer0_block0.payload.bin \
  --target fixtures/native_valid/target_profile.json

bifrostd/target/debug/bifrost-xfer get \
  --endpoint 127.0.0.1:7420 \
  --object-id bifrost://object/blake3/<object-id-hex> \
  --out /tmp/bifrost-get
```

`HAS` and `GET` serve only committed, catalog-consistent objects. See the
[transport protocol](docs/phase2_protocol.md) and
[store CLI](docs/phase3_store_cli.md) for the full command surface.

## Validation and Tests

The default suites are CPU-only and local. They do not require vLLM, LMCache,
a GPU, model downloads, cloud credentials, Docker, or root access.

```sh
python -m pip install -e "bifrost_py[dev]" \
  -e "contextstorm[dev]" \
  -e "integrations/lmcache_bifrost[dev]" \
  -e "integrations/vllm_bifrost[dev]"

pytest bifrost_py/tests tests \
  integrations/lmcache_bifrost/tests \
  integrations/vllm_bifrost/tests
cargo test --manifest-path bifrostd/Cargo.toml
(cd contextstorm && PYTHONPATH=. pytest)
```

Validate an object directly:

```sh
bifrost-kv validate \
  --meta fixtures/native_valid/tiny_gpt_layer0_block0.meta.json \
  --payload fixtures/native_valid/tiny_gpt_layer0_block0.payload.bin \
  --target fixtures/native_valid/target_profile.json
```

Python is the reference validator; Rust mirrors metadata parsing, validation
reason codes, hashes, object IDs, and fixture behavior. See the
[KV object format](docs/kv_object_format.md),
[identity contract](docs/object_identity.md), and
[validation errors](docs/validation_errors.md).

## Limitations

- The direct vLLM connector does not implement the load path and deliberately
  rejects real-vLLM transfer hooks that could imply working end-to-end KV
  transfer.
- There is no GPU-resident vLLM or SGLang extraction/injection, GPU-direct
  transport, or copy-on-write integration. Direct connector payloads are
  CPU-staged opaque bytes.
- The LMCache integration uses LMCache's remote-storage boundary; LMCache owns
  tensor layout, cache chunking, and engine rehydration semantics.
- The tiny-transformer harness proves the native object contract on a small,
  deterministic CPU model. It is not a claim of production-model format
  compatibility.
- The recorded Phase 6 evidence is a single-host result and did not show a
  speedup.
- RDMA, QUIC, compression, parity/FEC, distributed routing, production
  authentication, and mandatory GPU CI are not implemented.

## Repository Guide

```text
bifrost_py/                 Python object model, validator, client, and serving tools
bifrostd/                   Rust validator, transport daemon, and durable store
integrations/lmcache_bifrost/
                            LMCache remote-storage connector
integrations/vllm_bifrost/  Experimental direct vLLM connector
contextstorm/               Deterministic workloads, faults, metrics, and reports
examples/                   Tiny-model, connector, and serving demonstrations
fixtures/                   Valid, invalid, and cross-language test vectors
docs/                       Contracts, phase designs, checklists, and runbooks
```
