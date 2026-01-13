"""Factory for building CPU/GPU runtimes based on configuration."""

from __future__ import annotations

from typing import Any

from src.common.config.config import Config
from src.common.storage.database import Database
from src.common.interfaces.runtime import ModeRuntime
from src.api.cpu.runtime import build_cpu_runtime
from src.api.services.worker_control import WorkerControlService


def build_mode_runtime(config: Config, db: Database, worker_control: WorkerControlService) -> ModeRuntime:
    """Build ModeRuntime based on backend configuration.

    The backend is automatically determined from runtime_config.json (created by
    scripts/setup/check_api_env.py). CPU backend uses text search only, GPU backends
    (metal/cuda) use vector search with graceful fallback to text search.

    Note: ROCm support is TBD and currently disabled.
    """
    backend = getattr(config, "backend", "cpu")
    if backend == "cpu":
        return build_cpu_runtime(config, db, worker_control)
    if backend == "rocm":
        raise RuntimeError(
            "ROCm/AMD GPU support is not yet available. "
            "Please use CPU mode, Apple Metal (macOS), or NVIDIA CUDA instead. "
            "AMD GPU support is planned for a future release."
        )
    # GPU backends (metal, cuda) all use the GPU runtime
    from src.api.gpu.runtime import build_gpu_runtime  # Lazy import so CPU mode avoids torch deps

    return build_gpu_runtime(config, db, worker_control)
