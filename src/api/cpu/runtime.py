"""CPU-only runtime builder (SQLite-only search)."""

from __future__ import annotations

from typing import Any

from src.common.config.config import Config
from src.common.storage.database import Database
from src.common.storage.schema import utc_now
from src.common.interfaces.runtime import (
    ModeRuntime,
    OperationsModeAdapter,
    DiagnosticsModeAdapter,
)
from src.api.services.search_service import SearchService


class CpuOperationsModeAdapter(OperationsModeAdapter):
    """Operations adapter for CPU mode (no vector jobs)."""

    def can_run_vector_jobs(self) -> bool:
        return False


class CpuDiagnosticsAdapter(DiagnosticsModeAdapter):
    """Diagnostics adapter for CPU-only mode (semantic search disabled)."""

    def faiss_status(self, data_path, session) -> dict:  # noqa: D401 - simple adapter
        del data_path, session
        return {
            "state": "info",
            "headline": "Semantic search disabled",
            "detail": "CPU-only mode (SQLite keyword search)",
            "validated_at": utc_now(),
        }


def build_cpu_runtime(config: Config, db: Database, worker_control) -> ModeRuntime:
    """Build CPU-only ModeRuntime."""
    del db, worker_control  # Not used in sqlite_only mode

    # SQLite-only search service using built-in text provider
    search_service = SearchService(
        primary_provider="sqlite_text",
        fallback_enabled=False,
        max_retries=0,
        vector_provider=None,
    )
    return ModeRuntime(
        search_service=search_service,
        thread_safe_faiss=None,
        operations_mode_adapter=CpuOperationsModeAdapter(),
        diagnostics_adapter=CpuDiagnosticsAdapter(),
        background_worker=None,
        worker_pool=None,
    )
