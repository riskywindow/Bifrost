# Phase 7 vLLM API Surface

Last verified: 2026-07-04

## API volatility

vLLM's KVTransfer surface is version-sensitive. Import paths, connector base
classes, `KVTransferConfig` fields, scheduler metadata, worker hooks, and
method signatures may differ across releases.

Phase 7 must not assume that one installed vLLM version defines the permanent
contract. The first implementation must include an inspector and must gate real
vLLM support on the installed API shape.

## API inspector

The inspector is required before connector behavior is trusted against real
vLLM. It should be safe to run across these outcomes:

1. No vLLM installed: report `status: "not_installed"` with a deterministic
   unsupported reason.
2. vLLM installed without the required KVTransfer surface: report
   `status: "partial"` with detected version and missing surface.
3. vLLM installed with the required KVTransfer surface: report
   `status: "installed"` with import paths, signatures, config fields,
   lifecycle hooks, CLI flags, and warnings.
4. Inspector-internal failures: report `status: "error"` when the failure can
   be represented, or exit with code 2 from the CLI boundary if the inspector
   itself cannot complete.

The command is:

```bash
python tools/bifrost_vllm_api_inspect.py --json
```

It may also write the JSON artifact:

```bash
python tools/bifrost_vllm_api_inspect.py \
  --json \
  --output runs/phase7-vllm-api-surface.json
```

The CLI exits with code 0 when inspection completes, including when vLLM is
absent. It exits with code 2 only for an internal inspector failure.

The inspector should record:

1. Python executable and version.
2. vLLM distribution version and module file paths.
3. Whether `vllm.distributed.kv_transfer` is importable, or the installed
   equivalent path.
4. Whether `vllm serve --help` exposes `--kv-transfer-config`, or the
   installed equivalent.
5. `KVTransferConfig` constructor fields, annotations, defaults, and unknown
   fields.
6. Connector base classes and required abstract methods when discoverable.
7. Lifecycle method names, signatures, coroutine status, and return
   conventions.
8. Scheduler metadata object names and public fields when discoverable.
9. Whether 1P1D or disaggregated prefill/decode helpers are importable.
10. Any detected incompatibility reason that should keep real smoke tests
    skipped.

The inspector must not import model weights, initialize CUDA, download assets,
or start a server.

## Inspector JSON contract

Phase 7 real-vLLM tests and smoke scripts must consume this inspector output
before they attempt to instantiate vLLM or the BIFROST connector against a
real vLLM installation. A real test must remain skipped unless the inspector
reports the exact API shape required by that test.

The top-level output includes:

1. `status`: `not_installed`, `installed`, `partial`, or `error`.
2. `vllm_version`: `vllm.__version__` or installed distribution metadata when
   available.
3. `imports`: import status, module paths, versions, and import errors.
4. `config_fields`: expected `KVTransferConfig` field presence and sources.
5. `connector_base_methods`: public callable method signatures on
   `KVConnectorBase_V1`, including the expected lifecycle methods when
   present.
6. `dynamic_connector_supported`: whether the installed config surface exposes
   `kv_connector`, `kv_connector_module_path`, and
   `kv_connector_extra_config` with an importable KVTransfer package.
7. `available_kv_connectors`: discoverable connector classes under
   `vllm.distributed.kv_transfer.kv_connector.v1`, when the package can be
   listed safely.
8. `warnings`: compatibility warnings that do not by themselves make the
   inspector fail.
9. `unsupported_reasons`: deterministic reasons real vLLM support should stay
   disabled.

The implementation entry points live in:

```text
bifrost_py/bifrost_vllm/api_inspector.py
tools/bifrost_vllm_api_inspect.py
```

The Python module exposes:

```text
has_vllm()
vllm_version()
inspect_kv_transfer_config()
inspect_kv_connector_base_v1()
inspect_dynamic_connector_support()
inspect_available_kv_connector_modules()
inspect_result()
```

When running on the Phase 6 GPU host with vLLM 0.10.2 installed, capture the
version-specific surface with:

```bash
python tools/bifrost_vllm_api_inspect.py \
  --json \
  --output examples/vllm_bifrost_connector/vllm_0_10_2_api_surface.json
```

That JSON artifact is optional and must not be required by default CI. If vLLM
is absent, the command should still complete with `status: "not_installed"`;
do not create or require a fake 0.10.2 artifact.

## Expected KVTransferConfig fields

The connector should be prepared to parse a version-sensitive configuration
that may include:

1. `kv_connector`: connector class name, import target, or registered name.
2. `kv_role`: role such as producer, consumer, prefill, decode, or both.
3. `kv_connector_module_path`: Python module path for dynamic import when
   supported.
4. `kv_connector_extra_config`: free-form connector configuration.
5. `kv_buffer_device`: staging device when vLLM exposes it.
6. `kv_connector_model_executable_path`, `kv_connector_rank`, or related
   distributed metadata when vLLM exposes it.
7. `is_kv_transfer_instance`: boolean marker used by some vLLM versions.

BIFROST-specific config should live under an explicit nested object or
connector extra config:

```text
endpoint
timeout_seconds
chunk_size
metrics_jsonl_path
strict_validation
layout_fingerprint
engine_version
model_id
model_hash
allow_real_vllm
```

Unknown fields from vLLM should be preserved in inspector output and ignored
only when they are not required for safe operation. Missing fields required for
identity, layout compatibility, or lifecycle safety must keep real vLLM
support disabled.

## Expected lifecycle methods

The initial connector should be ready to support the lifecycle shape currently
used by vLLM KVTransfer examples:

```text
__init__
register_kv_caches
save_kv_layer
wait_for_save
start_load_kv
wait_for_layer_load
get_block_ids_with_load_errors
request_finished
shutdown
```

The fake lifecycle should use these method names even when real vLLM is not
installed. If an installed vLLM version uses different names or signatures, the
inspector must report the delta and the real smoke tests must skip until the
connector has an explicit compatibility adapter.

## Real-vLLM support gates

Real vLLM support is enabled only when all of the following are true:

1. The operator sets the exact opt-in environment variables documented in
   `docs/phase7_real_vllm_smoke.md`.
2. The inspector reports a compatible KVTransfer surface.
3. The connector dynamic import target resolves.
4. The BIFROST endpoint is configured and reachable for tests that touch the
   daemon.
5. Any required model path is local and already available.
6. The test is not running in default CI.

When any gate is missing, the real test must skip with a precise reason. It
must not silently fall back to fake success.
