"""Admin endpoints for index management and monitoring"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Any
import logging

from src.api.dependencies import get_db_session, get_search_service, get_config

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


# Queue management endpoints (Phase 4)

@router.get("/queue/status")
def get_queue_status() -> Dict[str, Any]:
    """
    Get queue status: pending jobs, failed jobs, worker health.

    Returns:
    - queue: Counts of pending and failed jobs
    - workers: Status of each worker (jobs processed, running state, etc.)
    """
    from src.api_server import worker_pool

    if not worker_pool:
        raise HTTPException(status_code=503, detail="Worker pool not initialized")

    worker_status = worker_pool.get_status()
    queue_depth = worker_pool.get_queue_depth()

    return {
        "queue": queue_depth,
        "workers": worker_status,
    }


@router.post("/queue/pause")
def pause_queue() -> Dict[str, str]:
    """
    Pause all workers.

    Use this before bulk imports or maintenance operations.
    """
    from src.api_server import worker_pool

    if not worker_pool:
        raise HTTPException(status_code=503, detail="Worker pool not initialized")

    worker_pool.pause_all()
    return {"status": "paused"}


@router.post("/queue/resume")
def resume_queue() -> Dict[str, str]:
    """Resume all workers."""
    from src.api_server import worker_pool

    if not worker_pool:
        raise HTTPException(status_code=503, detail="Worker pool not initialized")

    worker_pool.resume_all()
    return {"status": "resumed"}


@router.post("/queue/retry-failed")
def retry_failed(session: Session = Depends(get_db_session)) -> Dict[str, Any]:
    """
    Retry all failed embeddings by resetting status to 'pending'.

    Returns:
    - retried: Counts of entries reset to pending
    """
    from src.storage.schema import Experience, CategoryManual

    # Reset experiences
    exp_count = session.query(Experience).filter(
        Experience.embedding_status == 'failed'
    ).update({"embedding_status": "pending"})

    # Reset manuals
    man_count = session.query(CategoryManual).filter(
        CategoryManual.embedding_status == 'failed'
    ).update({"embedding_status": "pending"})

    session.commit()

    return {
        "retried": {
            "experiences": exp_count,
            "manuals": man_count,
            "total": exp_count + man_count,
        }
    }


@router.post("/queue/drain")
def drain_queue(timeout: int = 300) -> Dict[str, Any]:
    """
    Wait for queue to be empty (all pending jobs processed).

    Args:
        timeout: Maximum seconds to wait (default 300 = 5 minutes)

    Returns:
        Status after drain attempt with elapsed time and remaining jobs
    """
    import time
    from src.api_server import worker_pool

    if not worker_pool:
        raise HTTPException(status_code=503, detail="Worker pool not initialized")

    start_time = time.time()
    while time.time() - start_time < timeout:
        depth = worker_pool.get_queue_depth()
        pending = depth["pending"]["total"]

        if pending == 0:
            return {
                "status": "drained",
                "elapsed": time.time() - start_time,
            }

        # Wait a bit before checking again
        time.sleep(5)

    # Timeout reached
    depth = worker_pool.get_queue_depth()
    return {
        "status": "timeout",
        "elapsed": timeout,
        "remaining": depth["pending"]["total"],
    }
