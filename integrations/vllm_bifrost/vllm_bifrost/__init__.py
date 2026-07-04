"""BIFROST Phase 7 vLLM KVTransfer connector package.

The package root intentionally avoids importing vLLM, LMCache, torch, CUDA
helpers, model assets, or connector runtime modules. CI-safe fake interfaces
live in :mod:`vllm_bifrost.fakes`.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
