"""Factory for building CPU/GPU runtimes based on configuration."""

from __future__ import annotations

from typing import Any

from src.common.config.config import Config
from src.common.storage.database import Database
from src.common.interfaces.runtime import ModeRuntime
from src.api.cpu.runtime import build_cpu_runtime
from src.api.gpu.runtime import build_gpu_runtime
from src.api.services.worker_control import WorkerControlService


def build_mode_runtime(config: Config, db: Database, worker_control: WorkerControlService) -> ModeRuntime:
    """Build ModeRuntime based on backend configuration.

    The backend is automatically determined from runtime_config.json (created by
    scripts/check_api_env.py). CPU backend uses text search only, GPU backends
    (metal/cuda/rocm) use vector search with graceful fallback to text search.
    """
    backend = getattr(config, "backend", "cpu")
    if backend == "cpu":
        return build_cpu_runtime(config, db, worker_control)
    # GPU backends (metal, cuda, rocm) all use the GPU runtime
    return build_gpu_runtime(config, db, worker_control)
