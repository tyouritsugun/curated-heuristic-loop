"""Shared runtime interfaces for CPU/GPU modes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Protocol


class OperationsModeAdapter(Protocol):
    """Mode-specific adapter for vector-capable operations."""

    def can_run_vector_jobs(self) -> bool: ...


class DiagnosticsModeAdapter(Protocol):
    """Mode-specific diagnostics for FAISS/semantic components."""

    def faiss_status(self, data_path: Path, session: Any) -> Dict[str, Any]: ...


@dataclass
class ModeRuntime:
    """Container for mode-specific runtime components."""

    search_service: Optional[Any]
    thread_safe_faiss: Optional[Any]
    operations_mode_adapter: Optional[OperationsModeAdapter] = None
    diagnostics_adapter: Optional[DiagnosticsModeAdapter] = None
    background_worker: Optional[Any] = None
    worker_pool: Optional[Any] = None
