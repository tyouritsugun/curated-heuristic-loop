"""Admin endpoints for index management and monitoring"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Dict, Any
import logging

from src.api.dependencies import (
    get_config,
    get_db_session,
    get_search_service,
    get_worker_control_service,
)
from src.api.services.worker_control import WorkerUnavailableError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/index/status")
def get_index_status(
    search_service=Depends(get_search_service),
    config=Depends(get_config)
) -> Dict[str, Any]:
    """Get FAISS index status for debugging and monitoring.

    Returns:
    - status: "available" or "unavailable"
    - index_size: Number of vectors in index
    - tombstone_ratio: Ratio of deleted entries (0.0-1.0)
    - needs_rebuild: Whether rebuild is needed
    - save_policy: Current save policy
    - rebuild_threshold: Threshold for automatic rebuild
    - model_name: Embedding model name
    - dimension: Vector dimension
    """
    # Get vector provider
    vector_provider = search_service.get_vector_provider()

    if not vector_provider or not vector_provider.is_available:
        return {
            "status": "unavailable",
            "reason": "FAISS not loaded or not available"
        }

    try:
        # Access the underlying ThreadSafeFAISSManager
        # vector_provider is VectorFAISSProvider which has index_manager
        faiss_manager = getattr(vector_provider, 'index_manager', None)

        if not faiss_manager:
            return {
                "status": "unavailable",
                "reason": "FAISS manager not found"
            }

        # Get underlying manager from ThreadSafeFAISSManager
        underlying_manager = getattr(faiss_manager, '_manager', faiss_manager)

        # Collect status information
        tombstone_ratio = faiss_manager.get_tombstone_ratio()
        needs_rebuild = faiss_manager.needs_rebuild()

        return {
            "status": "available",
            "index_size": underlying_manager.index.ntotal,
            "tombstone_ratio": round(tombstone_ratio, 4),
            "needs_rebuild": needs_rebuild,
            "save_policy": config.faiss_save_policy,
            "rebuild_threshold": config.faiss_rebuild_threshold,
            "model_name": underlying_manager.model_name,
            "dimension": underlying_manager.dimension,
        }

    except Exception as e:
        logger.exception("Error getting index status")
        raise HTTPException(status_code=500, detail=f"Failed to get index status: {e}")


@router.post("/index/rebuild")
def trigger_rebuild(
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service)
) -> Dict[str, Any]:
    """Manually trigger FAISS index rebuild.

    Warning: This is a blocking operation and may take several seconds.
    Use with caution in production.

    Returns:
    - status: "success" or "error"
    - message: Human-readable message
    - vectors_count: Number of vectors after rebuild (on success)
    """
    # Get vector provider
    vector_provider = search_service.get_vector_provider()

    if not vector_provider or not vector_provider.is_available:
        raise HTTPException(
            status_code=503,
            detail="FAISS not available for rebuild"
        )

    try:
        # Access the ThreadSafeFAISSManager
        faiss_manager = getattr(vector_provider, 'index_manager', None)

        if not faiss_manager:
            raise HTTPException(
                status_code=503,
                detail="FAISS manager not found"
            )

        # Trigger rebuild
        logger.info("Manual index rebuild triggered via admin endpoint")
        faiss_manager._rebuild_index()

        # Get new index size
        underlying_manager = getattr(faiss_manager, '_manager', faiss_manager)
        new_size = underlying_manager.index.ntotal

        logger.info(f"Manual rebuild completed: {new_size} vectors")

        return {
            "status": "success",
            "message": f"Index rebuilt successfully",
            "vectors_count": new_size
        }

    except Exception as e:
        logger.exception("Error during manual rebuild")
        raise HTTPException(
            status_code=500,
            detail=f"Rebuild failed: {str(e)}"
        )


@router.post("/index/save")
def trigger_save(
    search_service=Depends(get_search_service)
) -> Dict[str, Any]:
    """Manually trigger FAISS index save.

    Useful when using manual save policy to explicitly persist changes.

    Returns:
    - status: "success" or "error"
    - message: Human-readable message
    """
    # Get vector provider
    vector_provider = search_service.get_vector_provider()

    if not vector_provider or not vector_provider.is_available:
        raise HTTPException(
            status_code=503,
            detail="FAISS not available for save"
        )

    try:
        # Access the ThreadSafeFAISSManager
        faiss_manager = getattr(vector_provider, 'index_manager', None)

        if not faiss_manager:
            raise HTTPException(
                status_code=503,
                detail="FAISS manager not found"
            )

        # Trigger save
        logger.info("Manual index save triggered via admin endpoint")
        faiss_manager.save()

        logger.info("Manual save completed")

        return {
            "status": "success",
            "message": "Index saved successfully"
        }

    except Exception as e:
        logger.exception("Error during manual save")
        raise HTTPException(
            status_code=500,
            detail=f"Save failed: {str(e)}"
        )


def _require_worker_pool(operation, worker_control, session: Session, actor: str):
    try:
        return operation(session=session, actor=actor)
    except WorkerUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Worker pool not initialized") from exc


@router.get("/queue/status")
def queue_status(
    session: Session = Depends(get_db_session),
    worker_control=Depends(get_worker_control_service),
) -> Dict[str, Any]:
    """Return embedding queue + worker status."""
    return worker_control.status(session)


@router.post("/queue/pause")
def queue_pause(
    session: Session = Depends(get_db_session),
    worker_control=Depends(get_worker_control_service),
    actor: str = Query("admin-api"),
) -> Dict[str, str]:
    """Pause background worker pool (if running)."""
    return _require_worker_pool(worker_control.pause, worker_control, session, actor)


@router.post("/queue/resume")
def queue_resume(
    session: Session = Depends(get_db_session),
    worker_control=Depends(get_worker_control_service),
    actor: str = Query("admin-api"),
) -> Dict[str, str]:
    """Resume background worker pool (if paused)."""
    return _require_worker_pool(worker_control.resume, worker_control, session, actor)


@router.post("/queue/drain")
def queue_drain(
    session: Session = Depends(get_db_session),
    worker_control=Depends(get_worker_control_service),
    timeout: int = Query(300, ge=1, le=3600),
    actor: str = Query("admin-api"),
) -> Dict[str, Any]:
    """Drain embedding queue (best-effort)."""
    def drain_op(session: Session, actor: str):
        return worker_control.drain(session, timeout=timeout, actor=actor)

    return _require_worker_pool(drain_op, worker_control, session, actor)


@router.post("/queue/retry-failed")
def retry_failed(session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """
    Retry all failed embeddings by resetting status to 'pending'.

    Returns:
    - retried: Counts of entries reset to pending
    """
    from src.common.storage.schema import Experience, CategorySkill

    # Reset experiences
    exp_count = session.query(Experience).filter(
        Experience.embedding_status == 'failed'
    ).update({"embedding_status": "pending"})

    # Reset skills
    skill_count = session.query(CategorySkill).filter(
        CategorySkill.embedding_status == 'failed'
    ).update({"embedding_status": "pending"})

    session.commit()

    return {
        "retried": {
            "experiences": exp_count,
            "skills": skill_count,
            "total": exp_count + skill_count,
        }
    }
