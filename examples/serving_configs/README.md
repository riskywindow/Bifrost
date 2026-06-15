# BIFROST Phase 6 Serving Configs

Generated for mode `lmcache_inprocess` with BIFROST endpoint `127.0.0.1:7744`.

These files are scaffolds for opt-in serving experiments:

- `bifrost_lmcache_inprocess.yaml`: LMCache in-process BIFROST remote storage example.
- `bifrost_lmcache_mp.yaml`: LMCache multiprocess BIFROST remote storage example.
- `vllm_serve_bifrost_lmcache.sh`: guarded vLLM serve command.
- `lmcache_server_bifrost.sh`: guarded LMCache multiprocess server command.
- `vllm_bench_serve_bifrost_lmcache.sh`: guarded benchmark client command.
- `serving.env`: non-secret environment values for local runs.

The generated scripts do not embed Hugging Face tokens or any private token
value. They refuse to run unless explicit opt-in environment variables are set,
and they refuse non-local model values unless `BIFROST_ALLOW_MODEL_DOWNLOADS=1`
is explicitly set by the user.

Version-sensitive fields:

- LMCache plugin field names vary by release; verify remote_storage_plugin and remote_url against the installed LMCache version.
- vLLM LMCache enablement flags vary by release; verify the generated vLLM command before an opt-in real run.
- Real serving may require GPU hardware, CUDA, local model assets, and compatible vLLM plus LMCache packages.
- The model value does not resolve to a local path; generated scripts refuse remote downloads unless BIFROST_ALLOW_MODEL_DOWNLOADS=1 is set.

BIFROST remains behind LMCache remote storage in these examples. They are not a
raw vLLM KVTransfer connector configuration.
