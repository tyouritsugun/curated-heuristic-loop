"""Health check endpoints."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from src.api.dependencies import get_db_session, get_search_service, get_config
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
    search_service=Depends(get_search_service)
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

    # Check FAISS index
    try:
        if hasattr(search_service, 'faiss_index_manager') and search_service.faiss_index_manager:
            if search_service.faiss_index_manager.is_available:
                index_size = search_service.faiss_index_manager.index.ntotal
                components["faiss_index"] = {
                    "status": "healthy",
                    "detail": f"{index_size} vectors"
                }
            else:
                components["faiss_index"] = {
                    "status": "degraded",
                    "detail": "FAISS not available, using text search fallback"
                }
                if overall_status == "healthy":
                    overall_status = "degraded"
        else:
            components["faiss_index"] = {
                "status": "degraded",
                "detail": "FAISS index manager not initialized"
            }
            if overall_status == "healthy":
                overall_status = "degraded"
    except Exception as e:
        logger.warning(f"FAISS health check failed: {e}")
        components["faiss_index"] = {"status": "degraded", "detail": str(e)}
        if overall_status == "healthy":
            overall_status = "degraded"

    # Check embedding model
    try:
        if hasattr(search_service, 'embedding_client') and search_service.embedding_client:
            # Check if model is loaded
            if hasattr(search_service.embedding_client, 'model') and search_service.embedding_client.model:
                model_info = "Model loaded"
                components["embedding_model"] = {
                    "status": "healthy",
                    "detail": model_info
                }
            else:
                components["embedding_model"] = {
                    "status": "degraded",
                    "detail": "Embedding client exists but model not loaded"
                }
                if overall_status == "healthy":
                    overall_status = "degraded"
        else:
            components["embedding_model"] = {
                "status": "degraded",
                "detail": "Embedding model not available"
            }
            if overall_status == "healthy":
                overall_status = "degraded"
    except Exception as e:
        logger.warning(f"Embedding model health check failed: {e}")
        components["embedding_model"] = {"status": "degraded", "detail": str(e)}
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
