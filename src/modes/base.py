from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any, TYPE_CHECKING, Dict, Tuple, Protocol
import logging

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from src.search.service import SearchService


class OperationsModeAdapter(Protocol):
    """Mode-specific adapter for vector-capable operations.

    Keeps OperationsService ignorant of CHL_SEARCH_MODE while allowing
    CPU-only modes to disable vector jobs.
    """

    def can_run_vector_jobs(self) -> bool:
        """Return True when vector/search jobs are allowed."""
        ...


@dataclass
class ModeRuntime:
    """Container for mode-specific runtime components.

    This keeps the search stack wiring (vector vs SQLite-only) out of the
    FastAPI bootstrap logic while still exposing the pieces the server needs.
    """

    search_service: Optional["SearchService"]
    thread_safe_faiss: Optional[Any]
    background_worker: Optional[Any]
    worker_pool: Optional[Any]
    operations_adapter: Optional[OperationsModeAdapter] = None

    def health_components(self, config) -> Tuple[Dict[str, Dict[str, str]], bool]:
        """Return FAISS/embedding health components and whether they degrade overall status.

        This centralizes CPU vs vector-mode behavior so callers don't need to
        re-encode search_mode semantics.
        """
        from src.config import SearchMode

        components: Dict[str, Dict[str, str]] = {}
        degraded = False

        try:
            mode_enum = getattr(config, "search_mode_enum", None)

            # CPU-only mode: components are intentionally disabled
            if mode_enum is SearchMode.SQLITE_ONLY:
                detail = "Intentional SQLite-only mode"
                components["faiss_index"] = {"status": "disabled", "detail": detail}
                components["embedding_model"] = {"status": "disabled", "detail": detail}
                return components, degraded

            # Vector-capable modes: inspect provider availability
            svc = self.search_service
            if svc and hasattr(svc, "get_vector_provider"):
                vector_provider = svc.get_vector_provider()
                if vector_provider and getattr(vector_provider, "is_available", False):
                    try:
                        index_mgr = getattr(vector_provider, "index_manager", None)
                        index_obj = getattr(index_mgr, "index", None)
                        index_size = getattr(index_obj, "ntotal", None)
                    except Exception:
                        index_size = None

                    components["faiss_index"] = {
                        "status": "healthy",
                        "detail": f"{index_size} vectors" if index_size is not None else "Index loaded",
                    }
                    components["embedding_model"] = {
                        "status": "healthy",
                        "detail": "Model loaded and operational",
                    }
                else:
                    components["faiss_index"] = {
                        "status": "degraded",
                        "detail": "FAISS not available, using text search fallback",
                    }
                    components["embedding_model"] = {
                        "status": "degraded",
                        "detail": "Embedding model not available",
                    }
                    degraded = True
            else:
                components["faiss_index"] = {
                    "status": "degraded",
                    "detail": "Search service not initialized",
                }
                components["embedding_model"] = {
                    "status": "degraded",
                    "detail": "Search service not initialized",
                }
                degraded = True
        except Exception as exc:  # pragma: no cover - defensive
            logger.info("Vector provider health inspection failed", exc_info=True)
            components["faiss_index"] = {"status": "degraded", "detail": str(exc)}
            components["embedding_model"] = {"status": "degraded", "detail": str(exc)}
            degraded = True

        return components, degraded


def build_mode_runtime(config, db, worker_control_service) -> ModeRuntime:
    """Build runtime components for the current search mode.

    Delegates to mode-specific builders and degrades gracefully by returning
    a runtime with search_service=None on failure.
    """
    from .sqlite_only.runtime import build_runtime as build_cpu_runtime
    from .vector.runtime import build_runtime as build_vector_runtime

    try:
        # Prefer the explicit helper if available
        is_cpu_only = getattr(config, "is_cpu_only", None)
        if callable(is_cpu_only) and config.is_cpu_only():
            return build_cpu_runtime(config, db, worker_control_service)
        # Default to vector/semantic-capable runtime
        return build_vector_runtime(config, db, worker_control_service)
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.error("Mode runtime initialization failed: %s", exc, exc_info=True)
        return ModeRuntime(
            search_service=None,
            thread_safe_faiss=None,
            background_worker=None,
            worker_pool=None,
        )
