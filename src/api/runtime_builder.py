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
    """Build ModeRuntime based on CHL_SEARCH_MODE.

    This function delegates to CPU or GPU runtime builders depending on
    configuration. GPU mode builder is expected to handle missing GPU
    prerequisites gracefully and fall back if needed.
    """
    mode = getattr(config, "search_mode", "auto")
    if mode == "cpu":
        return build_cpu_runtime(config, db, worker_control)
    # Default to GPU-capable runtime for "auto"
    return build_gpu_runtime(config, db, worker_control)
