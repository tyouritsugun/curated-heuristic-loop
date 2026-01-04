"""Search endpoints and diagnostics."""

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
import logging

from src.api.dependencies import get_search_service, get_db_session, get_config
from src.api.models import (
    DuplicateCheckRequest,
    DuplicateCheckResponse,
    DuplicateCandidateResponse,
    UnifiedSearchRequest,
    UnifiedSearchResponse,
    UnifiedSearchResult,
)
from src.api.services.snippet import generate_snippet, extract_heading
from src.api.services.session_store import get_session_store
from src.common.storage.schema import Experience, CategorySkill
from src.common.storage.repository import ExperienceRepository, CategorySkillRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.post("/", response_model=UnifiedSearchResponse)
def unified_search(
    request: UnifiedSearchRequest,
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
    config=Depends(get_config),
    x_chl_session: Optional[str] = Header(None, alias="X-CHL-Session"),
):
    """
    Unified search API v1.1 with snippets, filtering, and session support.

    Returns rich results with snippets to reduce token usage for LLM contexts.
    Supports cross-type search, filtering, and session-based ranking.
    """
    try:
        if search_service is None:
            raise HTTPException(status_code=503, detail="Search service not initialized")

        if request.types and "skill" in request.types and not getattr(config, "skills_enabled", True):
            if request.types == ["skill"]:
                raise HTTPException(status_code=404, detail="Skills are disabled")
            request.types = [t for t in request.types if t != "skill"]

        # Session ID resolution: header takes precedence over body
        session_id = x_chl_session or request.session_id

        # Session filtering implementation
        # Get viewed IDs from session store if session_id provided
        viewed_ids = set()
        session_applied = False

        if session_id:
            store = get_session_store()
            viewed_ids = store.get_viewed_ids(session_id)
            session_applied = (request.hide_viewed or request.downrank_viewed) and len(viewed_ids) > 0

        # Backfill logic for hide_viewed
        # When hide_viewed is active, fetch extra results to account for filtering
        # Strategy: fetch 2x limit initially, then filter to target limit
        initial_limit = request.limit
        if session_id and viewed_ids and request.hide_viewed:
            # Fetch extra results (2x limit, capped at 50 to avoid excessive DB load)
            fetch_limit = min(initial_limit * 2, 50)
        else:
            fetch_limit = initial_limit

        # Perform unified search
        search_result = search_service.unified_search(
            session=session,
            query=request.query,
            types=request.types,
            category_code=request.category,
            limit=fetch_limit,
            offset=request.offset,
            min_score=request.min_score,
            filters=request.filters,
        )

        # Apply session filtering to results
        pre_filter_total = search_result["total"]
        if session_id and viewed_ids:
            results = search_result["results"]

            # Apply hide_viewed: remove viewed entries
            if request.hide_viewed:
                before_count = len(results)
                results = [r for r in results if r.entity_id not in viewed_ids]
                filtered_count = before_count - len(results)

                # Trim to requested limit after filtering
                results = results[:initial_limit]

                # Adjust total to account for filtered results
                # Approximation: We fetched fetch_limit results and filtered filtered_count.
                # Extrapolate to estimate how many viewed IDs might exist in the full result set.
                # Formula: total_filtered_estimate = (filtered_count / fetch_limit) * pre_filter_total
                # Adjusted total = pre_filter_total - total_filtered_estimate
                #
                # Guard: Only apply extrapolation if we have a reasonable sample size (>= 5)
                # to avoid wild swings on tiny samples (e.g., limit=1 → fetch_limit=2).
                MIN_SAMPLE_SIZE = 5
                if fetch_limit >= MIN_SAMPLE_SIZE:
                    filter_rate = filtered_count / fetch_limit
                    estimated_total_filtered = int(filter_rate * pre_filter_total)
                    search_result["total"] = max(0, pre_filter_total - estimated_total_filtered)
                elif fetch_limit > 0:
                    # Small sample: use conservative simple subtraction
                    search_result["total"] = max(0, pre_filter_total - filtered_count)
                else:
                    search_result["total"] = pre_filter_total

            # Apply downrank_viewed: multiply score by 0.5 for viewed entries
            # Note: downrank_viewed does NOT adjust total, as no results are removed.
            # Total still reflects the full result set; only scores/ranks change.
            if request.downrank_viewed and not request.hide_viewed:
                for r in results:
                    if r.entity_id in viewed_ids:
                        r.score = (r.score or 0.0) * 0.5

                # Re-sort by score and reassign ranks after downranking
                results.sort(key=lambda x: x.score or 0.0, reverse=True)
                for i, r in enumerate(results):
                    r.rank = i

            search_result["results"] = results

        # Build response with rich metadata and snippets
        # TODO: Consider optimizing N+1 queries here
        # Current flow: unified_search returns IDs → fetch each entity for snippets
        # With limit=25 max, this is 25 queries (acceptable for local tool)
        # Optimization: batch fetch entities via `WHERE id IN (...)` if needed
        exp_repo = ExperienceRepository(session)
        skill_repo = CategorySkillRepository(session)

        formatted_results = []
        for r in search_result["results"]:
            # Fetch entity for snippet generation
            if r.entity_type == "experience":
                entity = exp_repo.get_by_id(r.entity_id)
                if not entity:
                    continue

                # Generate heading and snippet
                heading = extract_heading(entity.playbook, fallback=entity.title)
                snippet, _ = generate_snippet(entity.playbook, max_length=request.snippet_len)

                result_dict = {
                    "entity_id": r.entity_id,
                    "entity_type": r.entity_type,
                    "title": entity.title,
                    "section": entity.section,
                    "score": r.score or 0.0,
                    "rank": r.rank,
                    "reason": getattr(r.reason, "value", str(r.reason)),
                    "provider": r.provider,
                    "degraded": r.degraded,
                    "hint": r.hint,
                    "heading": heading,
                    "snippet": snippet,
                    "author": entity.author,
                    "updated_at": entity.updated_at.isoformat() if entity.updated_at else None,
                }

                # Add full bodies if requested via fields
                if request.fields and "playbook" in request.fields:
                    result_dict["playbook"] = entity.playbook
                if request.fields and "context" in request.fields:
                    result_dict["context"] = entity.context

            elif r.entity_type == "skill":
                entity = skill_repo.get_by_id(r.entity_id)
                if not entity:
                    continue

                # Generate heading and snippet
                heading = extract_heading(entity.content, fallback=entity.name)
                snippet, _ = generate_snippet(entity.content, max_length=request.snippet_len)

                result_dict = {
                    "entity_id": r.entity_id,
                    "entity_type": r.entity_type,
                    "title": entity.name,
                    "section": None,  # Skills don't have sections
                    "score": r.score or 0.0,
                    "rank": r.rank,
                    "reason": getattr(r.reason, "value", str(r.reason)),
                    "provider": r.provider,
                    "degraded": r.degraded,
                    "hint": r.hint,
                    "heading": heading,
                    "snippet": snippet,
                    "author": entity.author,
                    "updated_at": entity.updated_at.isoformat() if entity.updated_at else None,
                }

                # Add full bodies if requested via fields
                if request.fields and "content" in request.fields:
                    result_dict["content"] = entity.content
                if request.fields and "description" in request.fields:
                    result_dict["description"] = entity.description

            else:
                continue

            formatted_results.append(UnifiedSearchResult(**result_dict))

        # Track viewed IDs in session store after building results
        if session_id and formatted_results:
            store = get_session_store()
            viewed_ids_to_add = {r.entity_id for r in formatted_results}
            store.add_viewed_ids(session_id, viewed_ids_to_add)

        # Add provider hints for degraded mode
        warnings = search_result["warnings"].copy()
        if search_result["degraded"]:
            warnings.append("Vector search unavailable; text fallback used")

        # Calculate top_score and has_more
        top_score = formatted_results[0].score if formatted_results else None
        has_more = (request.offset + len(formatted_results)) < search_result["total"]

        return UnifiedSearchResponse(
            results=formatted_results,
            count=len(formatted_results),
            total=search_result["total"],
            has_more=has_more,
            top_score=top_score,
            warnings=warnings,
            session_applied=session_applied,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error performing unified search")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/health")
def search_health(
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
    config=Depends(get_config),
) -> Dict[str, Any]:
    """
    Return search stack health information.

    Mirrors the information previously produced by `scripts/ops/search_health.py`:
    - Total counts for experiences/skills
    - Embedding status summary
    - FAISS availability and basic stats (GPU mode only)
    - Warnings for pending/failed embeddings or missing FAISS
    """
    # Base report structure
    report: Dict[str, Any] = {
        "totals": {"experiences": 0, "skills": 0},
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
    report["totals"]["skills"] = 0
    if getattr(config, "skills_enabled", True):
        report["totals"]["skills"] = session.query(CategorySkill).count()

    # Embedding status (entity tables as source of truth)
    pending = session.query(Experience).filter(Experience.embedding_status == "pending").count()
    embedded = session.query(Experience).filter(Experience.embedding_status == "embedded").count()
    failed = session.query(Experience).filter(Experience.embedding_status == "failed").count()
    if getattr(config, "skills_enabled", True):
        pending += session.query(CategorySkill).filter(CategorySkill.embedding_status == "pending").count()
        embedded += session.query(CategorySkill).filter(CategorySkill.embedding_status == "embedded").count()
        failed += session.query(CategorySkill).filter(CategorySkill.embedding_status == "failed").count()
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


@router.post("/duplicates", response_model=DuplicateCheckResponse)
def find_duplicates(
    request: DuplicateCheckRequest,
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
    config=Depends(get_config),
) -> DuplicateCheckResponse:
    """
    Lightweight duplicate check for a proposed entry.

    This endpoint is intended for MCP/LLM use before writing entries. It does
    not block writes and is purely advisory.
    """
    if search_service is None:
        raise HTTPException(status_code=503, detail="Search service not initialized")

    try:
        if request.entity_type == "skill" and not getattr(config, "skills_enabled", True):
            raise HTTPException(status_code=404, detail="Skills are disabled")
        candidates = search_service.find_duplicates(
            session=session,
            title=request.title,
            content=request.content,
            entity_type=request.entity_type,
            category_code=request.category_code,
            exclude_id=None,
            threshold=request.threshold,
        )

        limit = request.limit or 1
        candidates = candidates[:limit]

        formatted = [
            DuplicateCandidateResponse(
                entity_id=c.entity_id,
                entity_type=c.entity_type,
                score=c.score,
                reason=getattr(c.reason, "value", str(c.reason)),
                provider=c.provider,
                title=c.title,
                summary=c.summary,
            )
            for c in candidates
        ]

        return DuplicateCheckResponse(candidates=formatted, count=len(formatted))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error performing duplicate check")
        raise HTTPException(status_code=500, detail=str(exc))
