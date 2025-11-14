from __future__ import annotations

import logging
from typing import Any

from src.search.service import SearchService
from src.api.metrics import metrics

from ..base import ModeRuntime, OperationsModeAdapter

logger = logging.getLogger(__name__)


class SqliteOnlyOperationsAdapter:
    """Operations adapter for CPU-only mode (no vector jobs)."""

    def can_run_vector_jobs(self) -> bool:
        return False


def build_runtime(config: Any, db: Any, worker_control_service: Any) -> ModeRuntime:
    """Build runtime components for sqlite_only (CPU-only) mode.

    Vector components are intentionally disabled; only SQLite text search is used.
    """
    del db, worker_control_service  # Not used in sqlite_only mode

    logger.info("Search mode=sqlite_only; vector components disabled.")
    search_service = SearchService(
        primary_provider="sqlite_text",
        fallback_enabled=False,
        max_retries=0,
        vector_provider=None,
    )
    logger.info("✓ Search service initialized with SQLite text search only")

    # Best-effort metric; do not fail startup if metrics backend is unavailable
    try:
        metrics.increment("search_mode_sqlite_only", 1)
    except Exception:
        pass

    return ModeRuntime(
        search_service=search_service,
        thread_safe_faiss=None,
        background_worker=None,
        worker_pool=None,
        operations_adapter=SqliteOnlyOperationsAdapter(),
    )

