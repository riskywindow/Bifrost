# BIFROST Integration Strategy

Last verified: 2026-05-27

## Purpose

This document defines the integration path for BIFROST. The goal is to connect the system to real ML infrastructure without overcommitting to unstable internals too early.

The strategy is:

```text
1. Build BIFROST as an independent object and transfer service.
2. Prove real KV correctness with a tiny-transformer harness.
3. Integrate with LMCache as a custom remote storage plugin.
4. Reach vLLM through LMCache first.
5. Treat direct vLLM KV connector integration as a stretch goal.
```

## Why LMCache first

LMCache is the best first integration surface because:

1. It already focuses on storing and reusing KV caches.
2. Its docs describe integration with vLLM, including cache lookup, retrieval of KV chunks, and injection into vLLM attention cache.
3. Its remote storage plugin docs define a custom ConnectorAdapter and RemoteConnector interface.
4. The RemoteConnector interface includes the exact operations BIFROST needs: exists, exists_sync, get, put, list, and close.
5. A BIFROST plugin can be isolated to a small Python package without forking LMCache.

This reduces integration risk and lets the project show a real serving-adjacent demo earlier.

## Why not direct vLLM first

vLLM has KV transfer configuration and connector infrastructure, including roles such as producer, consumer, and both, and a `kv_connector_module_path` for dynamic loading in vLLM V1. That is valuable, but it is a more complex first target because direct connector work may depend on deeper vLLM internals and exact version behavior.

The direct vLLM connector is therefore a stretch goal. The initial vLLM path should be:

```text
vLLM -> LMCache -> BIFROST remote storage plugin -> bifrostd
```

## Integration architecture

```text
+-------------------------------+
| vLLM serving worker           |
| prompt processing             |
| KV cache injection            |
+---------------+---------------+
                |
                | LMCache connector path
                v
+-------------------------------+
| LMCache                       |
| chunk lookup and storage      |
+---------------+---------------+
                |
                | custom remote storage plugin
                v
+-------------------------------+
| lmcache_bifrost               |
| ConnectorAdapter              |
| RemoteConnector               |
+---------------+---------------+
                |
                | BIFROST Python client
                v
+-------------------------------+
| bifrostd                      |
| object store                  |
| hash verification             |
| multipath transfer            |
+-------------------------------+
```

## Integration package layout

```text
integrations/
  lmcache_bifrost/
    pyproject.toml
    lmcache_bifrost/
      __init__.py
      adapter.py
      connector.py
      client_bridge.py
      serialization.py
      config.py
    examples/
      lmcache_bifrost.yaml
      run_vllm_lmcache_bifrost.sh
    tests/
      test_adapter_loads.py
      test_put_get_roundtrip.py
      test_exists_sync.py
      test_missing_key_returns_none.py
      test_corrupt_object_rejected.py
```

## LMCache adapter design

LMCache remote storage plugin docs specify an adapter that defines a URL scheme and creates a RemoteConnector.

BIFROST adapter:

```python
PLUGIN_TYPE = "bifrost"

class BifrostConnectorAdapter(ConnectorAdapter):
    def __init__(self) -> None:
        super().__init__("bifrost://")

    def can_parse(self, url: str) -> bool:
        if url.startswith(self.schema):
            return True
        if url.startswith("plugin://"):
            return extract_plugin_type(url[len("plugin://"):]) == PLUGIN_TYPE
        return False

    def create_connector(self, context: ConnectorContext) -> RemoteConnector:
        return BifrostRemoteConnector(
            config=context.config,
            metadata=context.metadata,
            plugin_name=context.plugin_name,
        )
```

## LMCache connector design

BIFROST connector maps LMCache operations to BIFROST calls.

```python
class BifrostRemoteConnector(RemoteConnector):
    async def exists(self, key: CacheEngineKey) -> bool:
        return await self.client.exists(engine_key_to_bifrost_key(key))

    def exists_sync(self, key: CacheEngineKey) -> bool:
        return self.client.exists_sync(engine_key_to_bifrost_key(key))

    async def get(self, key: CacheEngineKey) -> Optional[MemoryObj]:
        payload = await self.client.get(engine_key_to_bifrost_key(key))
        if payload is None:
            return None
        return deserialize_memory_obj(payload)

    async def put(self, key: CacheEngineKey, memory_obj: MemoryObj):
        payload = serialize_memory_obj(memory_obj)
        metadata = make_opaque_lmcache_metadata(key, memory_obj, payload)
        await self.client.put(engine_key_to_bifrost_key(key), payload, metadata)

    async def list(self) -> list[str]:
        return await self.client.list(prefix="lmcache")

    async def close(self):
        await self.client.close()
```

Phase 1 implementation may need to adjust serialization based on LMCache MemoryObj details. The integration should isolate that in `serialization.py` so BIFROST core remains stable.

## LMCache configuration example

```yaml
chunk_size: 64
local_cpu: false
max_local_cpu_size: 5
remote_storage_plugins: ["bifrost"]
extra_config:
  remote_storage_plugin.bifrost.module_path: lmcache_bifrost.adapter
  remote_storage_plugin.bifrost.class_name: BifrostConnectorAdapter
  remote_storage_plugin.bifrost.endpoint: "127.0.0.1:7744"
  remote_storage_plugin.bifrost.durability: "verified"
  remote_storage_plugin.bifrost.object_type: "opaque_engine_blob"
```

This follows the documented LMCache pattern for remote storage plugins, where custom connectors are referenced through `remote_storage_plugins` and `extra_config` with module path and class name.

## BIFROST client API needed by the plugin

```python
class BifrostClient:
    async def put(self, key: str, payload: bytes, metadata: dict) -> PutResult: ...
    async def get(self, key: str, profile: dict | None = None) -> bytes | None: ...
    async def exists(self, key: str, profile: dict | None = None) -> bool: ...
    async def list(self, prefix: str | None = None) -> list[str]: ...
    async def close(self) -> None: ...

    def exists_sync(self, key: str, profile: dict | None = None) -> bool: ...
```

## Tiny-transformer harness integration

The tiny-transformer harness is independent of LMCache and vLLM. It proves that BIFROST can move real KV tensors correctly.

Path:

```text
prompt -> worker A prefill -> extract KV -> BIFROST PUT
BIFROST GET -> worker B inject KV -> continue decode
baseline prompt -> generate same continuation
compare logits and generated tokens
```

Files:

```text
integrations/tiny_transformer/
  extract_kv.py
  inject_kv.py
  roundtrip_test.py
  compare_logits.py
  model_profile.py
```

This harness protects the project if LMCache or vLLM APIs shift.

## vLLM through LMCache demo

Once the LMCache plugin works, run a vLLM plus LMCache demo.

Expected flow:

```text
1. Start bifrostd.
2. Start vLLM with LMCache enabled.
3. Configure LMCache to use lmcache_bifrost as remote storage.
4. Send a long prompt.
5. Observe cache miss and BIFROST PUT calls.
6. Send repeated or overlapping prompt.
7. Observe cache hit and BIFROST GET calls.
8. Inject network fault.
9. Observe BIFROST path adaptation and verified object loads.
```

## Direct vLLM connector stretch path

A direct connector could eventually use vLLM KV transfer facilities.

Potential path:

```text
vLLM KVTransferConfig -> dynamic connector module -> BIFROST connector -> bifrostd
```

This is not required for MVP.

Reasons to delay:

1. Higher API coupling.
2. More version sensitivity.
3. Harder debugging.
4. LMCache already gives a serious path to KV reuse and vLLM integration.

## Versioning strategy

Pin versions in benchmark runs:

```text
Python
PyTorch
vLLM
LMCache
CUDA if used
bifrostd commit
bifrost_py commit
lmcache_bifrost commit
```

Store these in:

```text
runs/<run_id>/system_versions.json
```

## Integration risks and mitigations

### Risk: LMCache MemoryObj serialization is not stable

Mitigation:

1. Keep serialization isolated.
2. Test roundtrip at the LMCache plugin layer.
3. Use opaque object mode first.
4. Pin LMCache version for benchmarks.

### Risk: LMCache plugin loader changes

Mitigation:

1. Keep adapter minimal.
2. Document exact version used.
3. Add a test that loads the adapter from config.

### Risk: vLLM plus LMCache requires more GPU memory than budget allows

Mitigation:

1. Use tiny-transformer harness for correctness.
2. Use smaller vLLM-compatible models.
3. Run final vLLM demo only after CPU-only phases pass.
4. Use synthetic ContextStorm results for transport claims.

### Risk: remote storage improves fault tolerance only in limited cases

Mitigation:

1. Avoid overclaiming.
2. Distinguish cache reuse from active decode migration.
3. Demonstrate process-level rehydration honestly.
4. Report whether generation resumes at a prompt boundary or mid-session.

## Integration acceptance criteria

LMCache plugin MVP passes when:

```text
[ ] adapter loads from LMCache config
[ ] connector can PUT and GET an opaque object through bifrostd
[ ] exists and exists_sync behave correctly
[ ] missing object returns None or false as appropriate
[ ] corrupted object is rejected by bifrostd
[ ] benchmark captures BIFROST events during LMCache usage
```

vLLM plus LMCache plus BIFROST demo passes when:

```text
[ ] first repeated-context request stores KV through BIFROST
[ ] second repeated-context request loads from BIFROST
[ ] TTFT or prefill work decreases on cache hit
[ ] BIFROST logs show verified GET and PUT operations
[ ] network fault does not corrupt returned objects
```

Direct vLLM connector stretch passes when:

```text
[ ] custom connector loads through vLLM config
[ ] connector can store or retrieve KV blocks
[ ] correctness tests pass on a small model
[ ] direct connector is compared with LMCache plugin path
```

## Implementation sequence

```text
1. Implement BIFROST Python client against a local mock daemon.
2. Implement lmcache_bifrost adapter and connector skeleton.
3. Add object serialization for opaque LMCache payloads.
4. Add PUT, GET, HAS tests without vLLM.
5. Add bifrostd real daemon behind the client.
6. Run LMCache plugin-level tests.
7. Run a vLLM plus LMCache example with BIFROST configured.
8. Add ContextStorm benchmark scenarios around that demo.
```

## Sources reviewed

- LMCache overview: https://docs.lmcache.ai/
- LMCache integration guide: https://docs.lmcache.ai/developer_guide/integration.html
- LMCache remote storage plugins: https://docs.lmcache.ai/developer_guide/extending_lmcache/remote_storage_plugins.html
- vLLM Production Stack KV cache sharing: https://docs.vllm.ai/projects/production-stack/en/latest/use_cases/sharing-kv-cache.html
- vLLM LMCache examples: https://docs.vllm.ai/en/latest/examples/disaggregated/lmcache/
- vLLM KV transfer config: https://docs.vllm.ai/en/v0.10.2/api/vllm/config/kv_transfer.html
