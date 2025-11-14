"""Health check endpoints."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
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

    # Check FAISS/embedding status via mode runtime (CPU vs vector-capable)
    if mode_runtime is not None:
        faiss_components, degraded = mode_runtime.health_components(config)
        components.update(faiss_components)
        if degraded and overall_status == "healthy":
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
