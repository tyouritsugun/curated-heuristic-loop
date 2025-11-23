"""Health check endpoints."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from pathlib import Path

from src.api.dependencies import get_db_session, get_search_service, get_config, get_mode_runtime
from src.api.models import HealthResponse
from src.api.metrics import metrics
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/", response_model=HealthResponse)
def health_check(
    config=Depends(get_config),
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
    mode_runtime=Depends(get_mode_runtime),
):
    """
    Health check endpoint reporting system status.

    Status levels:
    - healthy: All critical components operational
    - degraded: Non-critical components failing (e.g., FAISS unavailable, falling back to text search)
    - unhealthy: Critical components failing (database, embedding model)

    Returns 200 for healthy/degraded, 503 for unhealthy.
    """
    components = {}
    overall_status = "healthy"

    # Check database
    try:
        from sqlalchemy import text
        session.execute(text("SELECT 1"))
        components["database"] = {"status": "healthy", "detail": "Connected"}
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        components["database"] = {"status": "unhealthy", "detail": str(e)}
        overall_status = "unhealthy"

    semantic_enabled = True
    if config and hasattr(config, "is_semantic_enabled"):
        try:
            semantic_enabled = config.is_semantic_enabled()
        except Exception:
            semantic_enabled = True

    # Check FAISS/embedding status via diagnostics adapter
    adapter = getattr(mode_runtime, "diagnostics_adapter", None) if mode_runtime else None
    if semantic_enabled and adapter and hasattr(adapter, "faiss_status"):
        try:
            faiss_path = Path(getattr(config, "faiss_index_path", getattr(config, "experience_root", "data")))
            faiss_status = adapter.faiss_status(faiss_path, session)
            components["faiss_index"] = {
                "status": faiss_status.get("state", "info"),
                "detail": faiss_status.get("detail"),
                "headline": faiss_status.get("headline"),
                "validated_at": faiss_status.get("validated_at"),
            }
            if faiss_status.get("state") == "warn" and overall_status == "healthy":
                overall_status = "degraded"
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to collect FAISS diagnostics: %s", exc)
            components["faiss_index"] = {"status": "unknown", "detail": str(exc)}

    if semantic_enabled and "faiss_index" not in components:
        components["faiss_index"] = {
            "status": "degraded",
            "detail": "Diagnostics unavailable; vector status unknown",
        }
        if overall_status == "healthy":
            overall_status = "degraded"

    if not semantic_enabled:
        components.setdefault(
            "faiss_index",
            {
                "status": "disabled",
                "detail": "Vector search disabled in CPU mode",
            },
        )
        components["embedding_model"] = {
            "status": "disabled",
            "detail": "Semantic stack disabled (CPU mode)",
        }
    else:
        vector_provider = None
        try:
            if search_service and hasattr(search_service, "get_vector_provider"):
                vector_provider = search_service.get_vector_provider()
        except Exception as exc:
            logger.debug("Vector provider probe failed: %s", exc)

        if vector_provider and getattr(vector_provider, "is_available", False):
            components["embedding_model"] = {
                "status": "healthy",
                "detail": getattr(config, "embedding_model", "configured"),
            }
        else:
            components["embedding_model"] = {
                "status": "degraded",
                "detail": "Vector provider unavailable; falling back to SQLite",
            }
            if overall_status == "healthy":
                overall_status = "degraded"

    # Add timestamp
    timestamp = datetime.now(timezone.utc).isoformat()

    response_data = HealthResponse(
        status=overall_status,
        components=components,
        timestamp=timestamp
    )

    # Return 503 if unhealthy
    if overall_status == "unhealthy":
        return Response(
            content=response_data.model_dump_json(),
            status_code=503,
            media_type="application/json"
        )

    return response_data


@router.get("/metrics")
def get_metrics():
    """Get current metrics snapshot."""
    return metrics.get_snapshot()
