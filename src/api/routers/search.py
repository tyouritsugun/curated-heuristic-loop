"""Search endpoints and diagnostics."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Any
import logging

from src.api.dependencies import get_search_service, get_db_session, get_config
from src.api.models import SearchRequest, SearchResponse
from src.common.storage.schema import Experience, CategoryManual

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.post("/", response_model=SearchResponse)
def search(
    request: SearchRequest,
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
):
    """
    Search for entries using semantic or text search.

    Note: Core read path remains `/api/v1/entries/read`; this endpoint
    is for explicit search requests.
    """
    try:
        if search_service is None:
            raise HTTPException(status_code=503, detail="Search service not initialized")

        results = search_service.search(
            session=session,
            query=request.query,
            entity_type=request.entity_type,
            category_code=request.category_code,
            top_k=request.limit or 10,
        )

        formatted_results = []
        for r in results:
            formatted_results.append(
                {
                    "entity_id": r.entity_id,
                    "entity_type": r.entity_type,
                    "score": r.score,
                    "reason": getattr(r.reason, "value", str(r.reason)),
                    "provider": r.provider,
                    "rank": r.rank,
                }
            )

        return SearchResponse(results=formatted_results, count=len(formatted_results))

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error performing search")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/health")
def search_health(
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
    config=Depends(get_config),
) -> Dict[str, Any]:
    """
    Return search stack health information.

    Mirrors the information previously produced by `scripts/search_health.py`:
    - Total counts for experiences/manuals
    - Embedding status summary
    - FAISS availability and basic stats (GPU mode only)
    - Warnings for pending/failed embeddings or missing FAISS
    """
    # Base report structure
    report: Dict[str, Any] = {
        "totals": {"experiences": 0, "manuals": 0},
        "embedding_status": {"pending": 0, "embedded": 0, "failed": 0},
        "faiss": {
            "available": False,
            "model": getattr(config, "embedding_model", None),
            "dimension": None,
            "vectors": 0,
            "tombstone_ratio": None,
            "needs_rebuild": None,
        },
        "warnings": [],
    }

    # Totals
    report["totals"]["experiences"] = session.query(Experience).count()
    report["totals"]["manuals"] = session.query(CategoryManual).count()

    # Embedding status (entity tables as source of truth)
    pending = (
        session.query(Experience)
        .filter(Experience.embedding_status == "pending")
        .count()
        + session.query(CategoryManual)
        .filter(CategoryManual.embedding_status == "pending")
        .count()
    )
    embedded = (
        session.query(Experience)
        .filter(Experience.embedding_status == "embedded")
        .count()
        + session.query(CategoryManual)
        .filter(CategoryManual.embedding_status == "embedded")
        .count()
    )
    failed = (
        session.query(Experience)
        .filter(Experience.embedding_status == "failed")
        .count()
        + session.query(CategoryManual)
        .filter(CategoryManual.embedding_status == "failed")
        .count()
    )
    report["embedding_status"] = {
        "pending": pending,
        "embedded": embedded,
        "failed": failed,
    }

    # FAISS diagnostics (GPU mode only)
    try:
        if search_service is not None:
            vector_provider = getattr(search_service, "get_vector_provider", lambda: None)()
        else:
            vector_provider = None

        if vector_provider and getattr(vector_provider, "is_available", False):
            faiss_manager = getattr(vector_provider, "index_manager", None)
            if faiss_manager:
                underlying = getattr(faiss_manager, "_manager", faiss_manager)
                report["faiss"]["available"] = True
                report["faiss"]["dimension"] = getattr(
                    underlying, "dimension", None
                )
                report["faiss"]["vectors"] = getattr(
                    underlying.index, "ntotal", 0
                )
                try:
                    report["faiss"]["tombstone_ratio"] = faiss_manager.get_tombstone_ratio()
                    report["faiss"]["needs_rebuild"] = faiss_manager.needs_rebuild()
                except Exception:
                    # Best-effort diagnostics; don't fail endpoint
                    pass
        else:
            report["warnings"].append(
                "Vector search unavailable. Install GPU/ML extras and run setup if semantic search is desired."
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("FAISS diagnostics failed: %s", exc)
        report["warnings"].append(
            "Failed to collect FAISS diagnostics; semantic search may be unavailable."
        )

    # Warning hints based on embedding status
    if pending:
        report["warnings"].append(f"{pending} entities have pending embeddings")
    if failed:
        report["warnings"].append(f"{failed} entities have failed embeddings")

    return report
