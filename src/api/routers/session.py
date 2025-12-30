"""Session management endpoints."""

from fastapi import APIRouter, HTTPException, Header
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
import logging

from src.api.services.session_store import get_session_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/session", tags=["session"])


class SessionInfoResponse(BaseModel):
    """Response model for GET /session."""
    session_id: str = Field(..., description="Session identifier")
    viewed_count: int = Field(..., description="Number of entries viewed in this session")
    last_accessed: float = Field(..., description="Unix timestamp of last access")


class CitedEntriesRequest(BaseModel):
    """Request model for POST /session/cited."""
    entity_ids: List[str] = Field(
        ...,
        description="List of entity IDs that were cited/viewed",
        min_items=1
    )


class CitedEntriesResponse(BaseModel):
    """Response model for POST /session/cited."""
    session_id: str = Field(..., description="Session identifier")
    added_count: int = Field(..., description="Number of IDs added to viewed set")
    total_viewed: int = Field(..., description="Total number of viewed IDs in session")


@router.get("/", response_model=SessionInfoResponse)
def get_session_info(
    x_chl_session: Optional[str] = Header(None, alias="X-CHL-Session")
) -> SessionInfoResponse:
    """Get session information.

    Returns viewed count and last accessed time for a session.
    If session_id is not provided, generates a new session ID.

    Args:
        x_chl_session: Optional session ID from header

    Returns:
        SessionInfoResponse with session details
    """
    store = get_session_store()

    # If no session ID provided, generate new one
    if not x_chl_session:
        session_id = store.generate_session_id()
        # Initialize empty session
        store.add_viewed_ids(session_id, set())
        info = store.get_session_info(session_id)

        return SessionInfoResponse(
            session_id=session_id,
            viewed_count=info['viewed_count'],
            last_accessed=info['last_accessed']
        )

    # Existing session
    info = store.get_session_info(x_chl_session)

    if info is None:
        # Session expired or not found - treat as new
        store.add_viewed_ids(x_chl_session, set())
        info = store.get_session_info(x_chl_session)

    return SessionInfoResponse(
        session_id=x_chl_session,
        viewed_count=info['viewed_count'],
        last_accessed=info['last_accessed']
    )


@router.post("/cited", response_model=CitedEntriesResponse)
def mark_entries_cited(
    request: CitedEntriesRequest,
    x_chl_session: Optional[str] = Header(None, alias="X-CHL-Session")
) -> CitedEntriesResponse:
    """Mark entries as cited/viewed in the current session.

    Used by clients to explicitly mark entries they've used, enabling
    hide_viewed and downrank_viewed filtering in subsequent searches.

    Args:
        request: CitedEntriesRequest with entity_ids
        x_chl_session: Session ID from header (required)

    Returns:
        CitedEntriesResponse with updated counts
    """
    if not x_chl_session:
        raise HTTPException(
            status_code=400,
            detail="X-CHL-Session header required for marking cited entries"
        )

    store = get_session_store()

    # Get current viewed count before adding
    info_before = store.get_session_info(x_chl_session)
    viewed_before = info_before['viewed_count'] if info_before else 0

    # Add entity IDs to session
    entity_id_set = set(request.entity_ids)
    store.add_viewed_ids(x_chl_session, entity_id_set)

    # Get updated count
    info_after = store.get_session_info(x_chl_session)
    viewed_after = info_after['viewed_count']

    return CitedEntriesResponse(
        session_id=x_chl_session,
        added_count=len(entity_id_set),
        total_viewed=viewed_after
    )


@router.delete("/")
def clear_session(
    x_chl_session: Optional[str] = Header(None, alias="X-CHL-Session")
) -> Dict[str, Any]:
    """Clear a session's viewed history.

    Args:
        x_chl_session: Session ID from header (required)

    Returns:
        Success status
    """
    if not x_chl_session:
        raise HTTPException(
            status_code=400,
            detail="X-CHL-Session header required for clearing session"
        )

    store = get_session_store()
    cleared = store.clear_session(x_chl_session)

    if not cleared:
        raise HTTPException(
            status_code=404,
            detail="Session not found or already expired"
        )

    return {"status": "cleared", "session_id": x_chl_session}


@router.get("/stats")
def get_session_stats() -> Dict[str, Any]:
    """Get session store statistics (for diagnostics).

    Returns:
        Statistics about active sessions
    """
    store = get_session_store()
    return store.get_stats()
