# BIFROST LMCache Plugin Examples

These examples exercise the Phase 5 LMCache remote storage plugin without
requiring vLLM, GPU hardware, model downloads, or a real LMCache install.

## Start bifrostd

Build the local Rust binaries if needed:

```bash
cargo build --manifest-path bifrostd/Cargo.toml --bins
```

Start a daemon with an empty local store:

```bash
rm -rf /tmp/bifrost-lmcache-demo
bifrostd/target/debug/bifrost-daemon \
  --listen 127.0.0.1:7744 \
  --spool /tmp/bifrost-lmcache-demo/spool
```

## Install the integration package

For local development, install the Python client and LMCache integration in
editable mode:

```bash
python -m pip install -e bifrost_py
python -m pip install -e integrations/lmcache_bifrost
```

LMCache itself is optional for the fake tests and this roundtrip script.

## Run the plugin roundtrip

Fake demo objects require the explicit pickle fallback opt-in:

```bash
python examples/lmcache_bifrost/plugin_roundtrip.py \
  --endpoint 127.0.0.1:7744 \
  --allow-pickle-fallback
```

Machine-readable output:

```bash
python examples/lmcache_bifrost/plugin_roundtrip.py \
  --endpoint 127.0.0.1:7744 \
  --allow-pickle-fallback \
  --json
```

The fallback is for local fake objects only. Do not enable it for untrusted
payloads or production LMCache traffic.

## Inspect the BIFROST store

Check daemon-backed store health:

```bash
bifrostd/target/debug/bifrost-store fsck \
  --endpoint 127.0.0.1:7744 \
  --check \
  --json
```

List LMCache opaque objects:

```bash
bifrostd/target/debug/bifrost-store opaque list \
  --endpoint 127.0.0.1:7744 \
  --engine-name lmcache \
  --integration-name lmcache_bifrost_remote_storage
```

## Run fake tests

Fake LMCache tests run in CI without installing LMCache:

```bash
PYTHONPATH=bifrost_py:integrations/lmcache_bifrost pytest \
  integrations/lmcache_bifrost/tests
PYTHONPATH=bifrost_py:integrations/lmcache_bifrost pytest \
  tests/test_lmcache_bifrost_plugin_roundtrip_script.py
```

Daemon-backed tests require built Rust binaries and skip when they are absent.

## Configure LMCache remote storage

Use the package as an LMCache remote storage plugin. A production-shaped example
is in:

```bash
integrations/lmcache_bifrost/examples/lmcache_config_bifrost.yaml
```

The important fields are:

```yaml
remote_storage_plugins:
  - bifrost
remote_storage_plugin:
  bifrost:
    module_path: lmcache_bifrost.adapter
    class_name: BifrostConnectorAdapter
    extra_config:
      endpoint: 127.0.0.1:7744
      allow_pickle_fallback: false
remote_url: bifrost://127.0.0.1:7744
```

LMCache configuration keys vary by release, so verify the exact shape against
the installed LMCache version. The pickle-enabled example is for local fake
objects only.

## Run optional real LMCache tests

Real LMCache coverage is intentionally optional and version-sensitive. Install
LMCache in the developer environment, then run:

```bash
PYTHONPATH=bifrost_py:integrations/lmcache_bifrost \
  BIFROST_RUN_REAL_LMCACHE_TESTS=1 \
  pytest integrations/lmcache_bifrost/tests/test_real_lmcache_optional.py

PYTHONPATH=bifrost_py:integrations/lmcache_bifrost \
  python examples/lmcache_bifrost/real_lmcache_smoke.py --compat-only --json
```

These tests must remain skipped in default CI unless that job explicitly
installs LMCache.

## Run optional vLLM smoke

The vLLM smoke is opt-in and requires a local model path. It must not download
models or tokenizers:

```bash
BIFROST_RUN_VLLM_SMOKE=1 \
BIFROST_VLLM_MODEL=/path/to/local/model \
examples/lmcache_bifrost/run_vllm_lmcache_bifrost_smoke.sh
```

The route is `vLLM -> LMCache -> BIFROST remote storage plugin -> bifrostd`.
It is not a raw vLLM KVTransfer connector.

## Run ContextStorm LMCache scenario

The default LMCache-style ContextStorm scenario is local, CPU-only, and uses
fake opaque objects:

```bash
contextstorm run contextstorm/scenarios/lmcache_connector_small_ci.yaml \
  --runs-root /tmp/contextstorm-runs \
  --run-id lmcache-connector-small
```
