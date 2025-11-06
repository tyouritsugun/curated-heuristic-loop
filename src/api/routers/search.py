"""Search endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from src.api.dependencies import get_search_service, get_db_session
from src.api.models import SearchRequest, SearchResponse
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.post("/", response_model=SearchResponse)
def search(
    request: SearchRequest,
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service)
):
    """
    Search for entries using semantic or text search.

    Note: This is a simplified endpoint. Full search capability
    is integrated into the read_entries endpoint.
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

        # Format results
        formatted_results = []
        for r in results:
            formatted_results.append({
                "entity_id": r.entity_id,
                "entity_type": r.entity_type,
                "score": r.score,
                "reason": getattr(r.reason, 'value', str(r.reason)),
                "provider": r.provider,
                "rank": r.rank,
            })

        return SearchResponse(results=formatted_results, count=len(formatted_results))

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error performing search")
        raise HTTPException(status_code=500, detail=str(e))
