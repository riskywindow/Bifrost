# Phase 6 Environment Doctor

Last verified: 2026-06-15

## Purpose

The Phase 6 environment doctor explains what can run on the current machine and
why optional components are skipped. It should be safe in CI and normal
developer shells: no model download, no server startup, no GPU allocation, no
root operation, and no network dependency are required for the default check.

The implementation lives in:

```text
bifrost_py/bifrost_serving/env_doctor.py
tools/bifrost_env_doctor.py
```

Run it with:

```bash
python tools/bifrost_env_doctor.py --endpoint 127.0.0.1:7420 --json
python tools/bifrost_env_doctor.py --endpoint 127.0.0.1:7420 --model /local/model/path --output-json runs/phase6-env-doctor/report.json
```

Exit codes:

1. `0`: `fake_ci_ready` is ready.
2. `1`: required fake-CI dependencies are missing.
3. `2`: CLI usage error or unexpected doctor failure.

## Checks

The doctor should report:

1. Python version and executable path.
2. Platform, OS, CPU, memory when available, and disk space for the selected
   run directory.
3. CUDA runtime visibility, NVIDIA driver visibility, and GPU names when
   available.
4. `torch` import status, version, CUDA availability, and CUDA device count.
5. `vllm` import status and version.
6. `lmcache` import status and version.
7. BIFROST Python client import status and version or source path.
8. LMCache BIFROST connector import status and adapter class availability.
9. `bifrostd` binary availability.
10. Optional live BIFROST daemon reachability for a configured endpoint.
11. BIFROST connector configuration parseability.
12. Local model path existence and readability when a model path is supplied.
13. Hugging Face token presence when a run explicitly requests token-gated
    model access.
14. Required port availability for vLLM, LMCache, BIFROST, and benchmark
    clients.
15. Writable output directory and minimum free disk space threshold.
16. Git commit, dirty-tree status, and repository path.

The doctor may inspect versions and paths. It must not download models,
contact Hugging Face, start vLLM, start Docker, mutate network settings, or
require root in the default path.

Optional components are reported as `not_ready`, `skipped`, or `unknown`
without failing fake-CI readiness. This includes vLLM, LMCache, CUDA, GPU
devices, Hugging Face tokens, local model assets, a live `bifrostd`, and
serving ports.

The JSON schema is intentionally simple:

```json
{
  "checks": {
    "python": {
      "status": "ready",
      "details": {}
    }
  },
  "readiness": {
    "fake_ci_ready": {
      "status": "ready",
      "reasons": [],
      "recommended_fixes": []
    }
  }
}
```

Each check may include `reason` and `recommended_fix` fields. Import probes
capture incidental import stdout and stderr in check details so `--json`
stdout remains parseable even when optional packages print during import.

## Readiness levels

### fake_ci_ready

The repository can run deterministic fake serving workloads in CI.

Required:

1. Supported Python version.
2. BIFROST Python packages import from source or editable install.
3. Fake workload generator and report generator import.
4. Writable run directory.
5. Enough disk space for fake payloads and reports.

Current implementation requirements:

1. Python 3.11 or newer.
2. `bifrost_client` imports from source or installation.
3. Output directory can be created and written.
4. Output directory has at least the configured free-space threshold.

Not required:

```text
GPU
CUDA
torch
vLLM
LMCache
Hugging Face token
model path
bifrostd
Docker
root
internet
```

### lmcache_connector_ready

The Phase 5 connector can be exercised against fake or real LMCache-shaped
objects and a local BIFROST daemon.

Required:

1. `bifrostd` binary or configured command.
2. Reachable BIFROST daemon endpoint, or enough information to start one.
3. BIFROST Python client import.
4. LMCache BIFROST connector import.
5. Connector config parseability.
6. Writable store and spool paths.

Current implementation checks:

1. BIFROST Python client import.
2. LMCache BIFROST connector package import.
3. Adapter class import from `lmcache_bifrost.adapter`.
4. Connector config dataclass construction for the configured endpoint.
5. `bifrost-daemon` binary on `PATH` or in `bifrostd/target/debug`.
6. Reachable configured daemon endpoint with `ping()` and `stats()`.
7. Writable output directory and free disk space.

Real LMCache is optional for this level unless the user explicitly requests a
real LMCache connector probe.

### vllm_import_ready

The environment can import vLLM and LMCache but may not be able to serve.

Required:

1. `vllm` import and version.
2. `lmcache` import and version.
3. `torch` import and version.
4. Config generator can produce version-sensitive warnings.

The doctor only checks imports here. It does not start vLLM and does not
validate serving configuration.

Not required:

```text
GPU serving readiness
local model path
open serving ports
```

### gpu_serving_ready

The environment appears capable of starting a local vLLM serving process.

Required:

1. `vllm_import_ready`.
2. CUDA or other selected serving backend visible to torch and vLLM.
3. At least one suitable GPU when the selected model requires GPU execution.
4. Local model path exists and is readable.
5. Serving ports are available.
6. Output directory has enough disk space for logs and metrics.

This level is still advisory until a serving process starts and answers a
readiness probe.

The doctor treats this level as `not_ready` when no local model path is
provided, CUDA is unavailable through torch, no torch GPU devices are visible,
or serving ports are occupied. This is advisory only and does not affect
`fake_ci_ready`.

### full_benchmark_ready

The environment can run all three Phase 6 baselines and produce a report.

Required:

1. `gpu_serving_ready`.
2. LMCache configuration for local or CPU storage validates for the installed
   version.
3. LMCache BIFROST remote storage configuration validates for the installed
   version.
4. BIFROST daemon endpoint is reachable or can be started.
5. vLLM benchmark client or `vllm bench serve` integration is available.
6. All baseline ports are free.
7. Report output directory is writable.

If any required check fails, the doctor should downgrade readiness and emit a
specific skip reason.

The current full benchmark readiness additionally requires the `vllm` CLI and
`vllm bench serve --help` to be available. If the CLI is unavailable, future
benchmark runner work may use a fallback client, but the doctor reports the
preferred path as `not_ready`.

## Testing

Default tests are CPU-only and local:

```bash
pytest -q tests/test_phase6_env_doctor.py
```

The tests cover missing optional vLLM and LMCache imports, fake-CI readiness,
absent daemon reporting, parseable JSON, mocked disk and port checks, and the
fact that GPU hardware is not required.
