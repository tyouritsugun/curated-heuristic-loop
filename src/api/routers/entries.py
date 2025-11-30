"""Entry endpoints for experiences and manuals."""

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
import logging

from src.api.dependencies import get_db_session, get_search_service, get_config
from src.api.models import (
    ReadEntriesRequest,
    ReadEntriesResponse,
    WriteEntryRequest,
    WriteEntryResponse,
    UpdateEntryRequest,
    UpdateEntryResponse,
)
from src.api.services.snippet import generate_snippet
from src.api.services.session_store import get_session_store
from src.common.storage.repository import (
    CategoryRepository,
    ExperienceRepository,
    CategoryManualRepository,
)
from src.common.dto.models import ExperienceWritePayload, format_validation_error, normalize_context
from pydantic import ValidationError as PydanticValidationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entries", tags=["entries"])


def _make_preview(text: str | None, limit: int = 320) -> tuple[str | None, bool]:
    """Return a truncated preview and whether truncation occurred.

    Note: Deprecated in favor of generate_snippet from snippet module.
    Kept for backward compatibility with existing code.
    """
    if text is None:
        return None, False
    trimmed = text.strip()
    if len(trimmed) <= limit:
        return trimmed, False
    return trimmed[:limit].rstrip() + "...", True


def _should_use_preview(fields: list[str] | None) -> bool:
    """Determine if preview mode should be used based on fields parameter."""
    # fields=None → full bodies (backward compatible)
    # fields=["preview"] → snippets only
    # fields with specific field names → include those fields
    return fields is not None and "preview" in fields


def _runtime_search_mode(config, search_service):
    mode = getattr(config, "search_mode", "auto")
    if mode != "auto":
        return mode
    provider_name = getattr(search_service, "primary_provider_name", None)
    vector_available = bool(getattr(search_service, "get_vector_provider", lambda: None)())
    if provider_name == "sqlite_text" and not vector_available:
        return "cpu"
    return mode


@router.post("/read", response_model=ReadEntriesResponse)
def read_entries(
    request: ReadEntriesRequest,
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
    config=Depends(get_config),
    x_chl_session: Optional[str] = Header(None, alias="X-CHL-Session"),
):
    """Read entries by query or IDs.

    Phase 2: Automatically tracks viewed entry IDs in session store when
    X-CHL-Session header is provided.
    """
    try:
        # Validate category exists (skip if None for global search)
        cat_repo = CategoryRepository(session)
        category = None
        if request.category_code is not None:
            category = cat_repo.get_by_code(request.category_code)
            if not category:
                raise HTTPException(
                    status_code=404,
                    detail=f"Category '{request.category_code}' not found"
                )

        limit = request.limit if request.limit is not None else (config.read_details_limit if config else 10)

        # Determine snippet length (default 320, or from request)
        snippet_len = request.snippet_len if request.snippet_len is not None else 320
        use_preview = _should_use_preview(request.fields)

        if request.entity_type not in {"experience", "manual"}:
            raise HTTPException(status_code=400, detail="Unsupported entity_type")

        if request.entity_type == "experience":
            exp_repo = ExperienceRepository(session)

            if request.query:
                # Semantic search
                if search_service is None:
                    raise HTTPException(status_code=503, detail="Search service not initialized")

                results = search_service.search(
                    session=session,
                    query=request.query,
                    entity_type='experience',
                    category_code=request.category_code,
                    top_k=limit,
                )

                entries = []
                for r in results:
                    exp = exp_repo.get_by_id(r.entity_id)
                    if not exp:
                        continue

                    # Use new snippet generation for v1.1
                    preview, truncated = generate_snippet(exp.playbook, max_length=snippet_len)

                    entry = {
                        "id": exp.id,
                        "title": exp.title,
                        "section": exp.section,
                        "embedding_status": getattr(exp, "embedding_status", None),
                        "updated_at": exp.updated_at,
                        "author": exp.author,
                        "source": exp.source,
                        "sync_status": exp.sync_status,
                        "score": r.score,
                        "reason": getattr(r.reason, 'value', str(r.reason)),
                        "provider": r.provider,
                        "rank": r.rank,
                        "degraded": getattr(r, "degraded", False),
                        "provider_hint": getattr(r, "hint", None),
                    }

                    # v1.1: Default to previews (cut tokens); full bodies only when explicitly requested
                    # fields=None → previews only (NEW default per plan)
                    # fields=["preview"] → previews only (explicit)
                    # fields=["playbook"] or ["playbook", "context"] → include requested full bodies

                    if request.fields is None or use_preview:
                        # Preview mode: snippets only
                        entry["playbook_preview"] = preview
                        entry["playbook_truncated"] = truncated
                    else:
                        # Explicit fields requested: include those
                        entry["playbook_preview"] = preview
                        entry["playbook_truncated"] = truncated

                        if "playbook" in request.fields:
                            entry["playbook"] = exp.playbook
                        if "context" in request.fields:
                            entry["context"] = normalize_context(exp.context)

                    entries.append(entry)
            else:
                # ID lookup or list all
                if request.ids:
                    # ID lookup works globally (IDs contain category prefix)
                    entities = [exp_repo.get_by_id(i) for i in request.ids]
                    entities = [e for e in entities if e is not None]
                else:
                    # List all requires category_code
                    if request.category_code is None:
                        raise HTTPException(
                            status_code=400,
                            detail="category_code required to list all entries (use query parameter for global search)"
                        )
                    all_exps = exp_repo.get_by_category(request.category_code)
                    entities = all_exps[:limit]

                entries = []
                for exp in entities:
                    # Generate preview
                    preview, truncated = generate_snippet(exp.playbook, max_length=snippet_len)

                    entry = {
                        "id": exp.id,
                        "title": exp.title,
                        "section": exp.section,
                        "embedding_status": getattr(exp, "embedding_status", None),
                        "updated_at": exp.updated_at,
                        "author": exp.author,
                        "source": exp.source,
                        "sync_status": exp.sync_status,
                        "reason": "id_lookup",
                        "provider": "direct",
                    }

                    # Apply same field logic as search path
                    if request.fields is None or use_preview:
                        entry["playbook_preview"] = preview
                        entry["playbook_truncated"] = truncated
                    else:
                        entry["playbook_preview"] = preview
                        entry["playbook_truncated"] = truncated

                        if "playbook" in request.fields:
                            entry["playbook"] = exp.playbook
                        if "context" in request.fields:
                            entry["context"] = normalize_context(exp.context)

                    entries.append(entry)

        else:  # manual
            man_repo = CategoryManualRepository(session)

            if request.query:
                # Semantic search for manuals
                if search_service is None:
                    raise HTTPException(status_code=503, detail="Search service not initialized")

                results = search_service.search(
                    session=session,
                    query=request.query,
                    entity_type='manual',
                    category_code=request.category_code,
                    top_k=limit,
                )

                entries = []
                for r in results:
                    man = man_repo.get_by_id(r.entity_id)
                    if not man:
                        continue

                    # Use new snippet generation for v1.1
                    preview, truncated = generate_snippet(man.content, max_length=snippet_len)

                    entry = {
                        "id": man.id,
                        "title": man.title,
                        "embedding_status": getattr(man, "embedding_status", None),
                        "updated_at": man.updated_at,
                        "author": man.author,
                        "score": r.score,
                        "reason": getattr(r.reason, 'value', str(r.reason)),
                        "provider": r.provider,
                        "rank": r.rank,
                        "degraded": getattr(r, "degraded", False),
                        "provider_hint": getattr(r, "hint", None),
                    }

                    # v1.1: Default to previews; full bodies only when explicitly requested
                    if request.fields is None or use_preview:
                        # Preview mode: snippets only
                        entry["content_preview"] = preview
                        entry["content_truncated"] = truncated
                    else:
                        # Explicit fields requested: include those
                        entry["content_preview"] = preview
                        entry["content_truncated"] = truncated

                        if "content" in request.fields:
                            entry["content"] = man.content
                        if "summary" in request.fields:
                            entry["summary"] = man.summary

                    entries.append(entry)
            else:
                # ID lookup or list all
                if request.ids:
                    # ID lookup works globally (IDs contain category prefix)
                    entities = [man_repo.get_by_id(i) for i in request.ids]
                    entities = [e for e in entities if e is not None]
                else:
                    # List all requires category_code
                    if request.category_code is None:
                        raise HTTPException(
                            status_code=400,
                            detail="category_code required to list all entries (use query parameter for global search)"
                        )
                    all_mans = man_repo.get_by_category(request.category_code)
                    entities = all_mans[:limit]

                entries = []
                for man in entities:
                    # Generate preview
                    preview, truncated = generate_snippet(man.content, max_length=snippet_len)

                    entry = {
                        "id": man.id,
                        "title": man.title,
                        "embedding_status": getattr(man, "embedding_status", None),
                        "updated_at": man.updated_at,
                        "author": man.author,
                        "reason": "id_lookup",
                        "provider": "direct",
                    }

                    # Apply same field logic as search path
                    if request.fields is None or use_preview:
                        entry["content_preview"] = preview
                        entry["content_truncated"] = truncated
                    else:
                        entry["content_preview"] = preview
                        entry["content_truncated"] = truncated

                        if "content" in request.fields:
                            entry["content"] = man.content
                        if "summary" in request.fields:
                            entry["summary"] = man.summary

                    entries.append(entry)

        # Phase 2: Track viewed entries in session store
        session_id = x_chl_session or request.session_id
        if session_id and entries:
            store = get_session_store()
            viewed_ids = {entry["id"] for entry in entries}
            store.add_viewed_ids(session_id, viewed_ids)

        meta = {
            "category": {"code": category.code, "name": category.name} if category else None,
            "search_mode": _runtime_search_mode(config, search_service),
        }

        return ReadEntriesResponse(entries=entries, count=len(entries), meta=meta)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error reading entries")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/write", response_model=WriteEntryResponse)
def write_entry(
    request: WriteEntryRequest,
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
    config=Depends(get_config),
):
    """Create a new entry.

    Phase 3: Automatically runs duplicate check with 750ms timeout.
    Decision tree:
    - Timeout → proceed with warning
    - Max score ≥ 0.85 → write, return duplicates + recommendation="review_first"
    - 0.50-0.84 → write, return duplicates as FYI
    - <0.50 → write normally
    """
    try:
        if request.entity_type == "experience":
            # Validate experience data before checking category to surface schema issues first
            try:
                validated = ExperienceWritePayload.model_validate({**request.data})
            except PydanticValidationError as e:
                raise HTTPException(status_code=400, detail=format_validation_error(e))

            # Validate category after payload passes basic checks
            cat_repo = CategoryRepository(session)
            category = cat_repo.get_by_code(request.category_code)
            if not category:
                raise HTTPException(
                    status_code=404,
                    detail=f"Category '{request.category_code}' not found"
                )

            # Phase 3: Auto-run duplicate check with hard 750ms timeout
            import time
            import threading
            duplicate_candidates = []
            duplicate_check_timeout = False

            if search_service is not None:
                # Run duplicate check with hard timeout using threading
                result_container = {"candidates": None, "error": None}

                def run_duplicate_check():
                    try:
                        result_container["candidates"] = search_service.find_duplicates(
                            session=session,
                            title=validated.title,
                            content=validated.playbook,
                            entity_type="experience",
                            category_code=request.category_code,
                            exclude_id=None,
                            threshold=0.50,
                        )
                    except Exception as e:
                        result_container["error"] = e

                thread = threading.Thread(target=run_duplicate_check, daemon=True)
                thread.start()
                thread.join(timeout=0.75)  # Hard 750ms timeout

                if thread.is_alive():
                    # Thread still running - timeout
                    logger.warning("Duplicate check timed out after 750ms")
                    duplicate_check_timeout = True
                elif result_container["error"]:
                    # Exception occurred
                    logger.warning("Duplicate check failed: %s", result_container["error"])
                    duplicate_check_timeout = True
                elif result_container["candidates"] is not None:
                    # Success
                    duplicate_candidates = result_container["candidates"]

            exp_repo = ExperienceRepository(session)
            new_obj = exp_repo.create({
                "category_code": request.category_code,
                "section": validated.section,
                "title": validated.title,
                "playbook": validated.playbook,
                "context": validated.context,
            })
            entry_id = new_obj.id

            # Build full entry for read-after-write
            entry_dict = {
                "id": new_obj.id,
                "title": new_obj.title,
                "playbook": new_obj.playbook,
                "context": normalize_context(new_obj.context),
                "section": new_obj.section,
                "embedding_status": getattr(new_obj, "embedding_status", None),
                "updated_at": new_obj.updated_at,
                "author": new_obj.author,
                "source": new_obj.source,
                "sync_status": new_obj.sync_status,
            }

            warnings: list[str] = []
            raw_context = request.data.get("context")
            if raw_context and validated.section in {"useful", "harmful"}:
                warnings.append(
                    "Context was ignored because section='useful' or 'harmful'; use section='contextual' if you need context."
                )

            # Phase 3: Apply decision tree based on duplicate check results
            recommendation = None
            duplicates_response = None

            if duplicate_check_timeout:
                warnings.append("duplicate_check_timeout=true")
            elif duplicate_candidates:
                # Find max score
                max_score = max(c.score for c in duplicate_candidates)

                # Decision tree:
                # - Max score ≥ 0.85 → recommendation="review_first"
                # - 0.50-0.84 → duplicates as FYI (no recommendation)
                # - <0.50 → no duplicates (already filtered by threshold=0.50)

                if max_score >= 0.85:
                    recommendation = "review_first"
                    warnings.append(f"Found {len(duplicate_candidates)} similar entries (max score: {max_score:.2f}). Review recommended.")
                else:
                    # Medium score (0.50-0.84): informational only
                    warnings.append(f"Found {len(duplicate_candidates)} potentially similar entries (max score: {max_score:.2f}).")

                # Format duplicates for response
                duplicates_response = [
                    {
                        "entity_id": c.entity_id,
                        "entity_type": c.entity_type,
                        "score": c.score,
                        "reason": getattr(c.reason, "value", str(c.reason)),
                        "provider": c.provider,
                        "title": c.title,
                        "summary": c.summary,
                    }
                    for c in duplicate_candidates
                ]

            return WriteEntryResponse(
                success=True,
                entry_id=entry_id,
                entry=entry_dict,
                duplicates=duplicates_response,
                recommendation=recommendation,
                warnings=warnings or None,
                message=(
                    "Experience created successfully. Indexing is in progress and may take up to 15 seconds. "
                    "Semantic search will not reflect this change until indexing is complete."
                ),
            )

        else:  # manual
            # Basic validation
            title = request.data.get("title")
            content = request.data.get("content")
            summary = request.data.get("summary")

            if not title or len(title) > 120:
                raise HTTPException(
                    status_code=400,
                    detail="Title must be 1-120 characters"
                )
            if not content:
                raise HTTPException(
                    status_code=400,
                    detail="Content cannot be empty"
                )

            cat_repo = CategoryRepository(session)
            category = cat_repo.get_by_code(request.category_code)
            if not category:
                raise HTTPException(
                    status_code=404,
                    detail=f"Category '{request.category_code}' not found"
                )

            # Phase 3: Auto-run duplicate check for manuals with hard 750ms timeout
            import threading
            duplicate_candidates = []
            duplicate_check_timeout = False

            if search_service is not None:
                # Run duplicate check with hard timeout using threading
                result_container = {"candidates": None, "error": None}

                def run_duplicate_check():
                    try:
                        result_container["candidates"] = search_service.find_duplicates(
                            session=session,
                            title=title,
                            content=content,
                            entity_type="manual",
                            category_code=request.category_code,
                            exclude_id=None,
                            threshold=0.50,
                        )
                    except Exception as e:
                        result_container["error"] = e

                thread = threading.Thread(target=run_duplicate_check, daemon=True)
                thread.start()
                thread.join(timeout=0.75)  # Hard 750ms timeout

                if thread.is_alive():
                    # Thread still running - timeout
                    logger.warning("Duplicate check timed out after 750ms")
                    duplicate_check_timeout = True
                elif result_container["error"]:
                    # Exception occurred
                    logger.warning("Duplicate check failed: %s", result_container["error"])
                    duplicate_check_timeout = True
                elif result_container["candidates"] is not None:
                    # Success
                    duplicate_candidates = result_container["candidates"]

            man_repo = CategoryManualRepository(session)
            new_manual = man_repo.create({
                "category_code": request.category_code,
                "title": title,
                "content": content,
                "summary": summary,
            })
            manual_id = new_manual.id

            manual_dict = {
                "id": new_manual.id,
                "title": new_manual.title,
                "content": new_manual.content,
                "summary": new_manual.summary,
                "embedding_status": getattr(new_manual, "embedding_status", None),
                "updated_at": new_manual.updated_at,
                "author": new_manual.author,
            }

            # Phase 3: Apply decision tree for manuals
            warnings: list[str] = []
            recommendation = None
            duplicates_response = None

            if duplicate_check_timeout:
                warnings.append("duplicate_check_timeout=true")
            elif duplicate_candidates:
                max_score = max(c.score for c in duplicate_candidates)

                if max_score >= 0.85:
                    recommendation = "review_first"
                    warnings.append(f"Found {len(duplicate_candidates)} similar entries (max score: {max_score:.2f}). Review recommended.")
                else:
                    # Medium score (0.50-0.84): informational only
                    warnings.append(f"Found {len(duplicate_candidates)} potentially similar entries (max score: {max_score:.2f}).")

                duplicates_response = [
                    {
                        "entity_id": c.entity_id,
                        "entity_type": c.entity_type,
                        "score": c.score,
                        "reason": getattr(c.reason, "value", str(c.reason)),
                        "provider": c.provider,
                        "title": c.title,
                        "summary": c.summary,
                    }
                    for c in duplicate_candidates
                ]

            return WriteEntryResponse(
                success=True,
                entry_id=manual_id,
                entry=manual_dict,
                duplicates=duplicates_response,
                recommendation=recommendation,
                warnings=warnings or None,
                message=(
                    "Manual created successfully. Indexing is in progress and may take up to 15 seconds. "
                    "Semantic search will not reflect this change until indexing is complete."
                ),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error writing entry")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update", response_model=UpdateEntryResponse)
def update_entry(
    request: UpdateEntryRequest,
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
    config=Depends(get_config),
):
    """Update an existing entry."""
    try:
        # Validate category
        cat_repo = CategoryRepository(session)
        category = cat_repo.get_by_code(request.category_code)
        if not category:
            raise HTTPException(
                status_code=404,
                detail=f"Category '{request.category_code}' not found"
            )

        if request.entity_type == "experience":
            allowed_fields = {"title", "playbook", "context", "section"}
            invalid_fields = set(request.updates.keys()) - allowed_fields
            if not request.updates or invalid_fields:
                if not request.updates:
                    raise HTTPException(status_code=400, detail="No updates provided")
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported update fields: {', '.join(sorted(invalid_fields))}"
                )

            exp_repo = ExperienceRepository(session)
            existing = exp_repo.get_by_id(request.entry_id)
            if not existing:
                raise HTTPException(
                    status_code=404,
                    detail=f"Entry '{request.entry_id}' not found"
                )

            effective_section = request.updates.get("section") or existing.section
            if request.updates.get("section") == "contextual" and not request.force_contextual:
                raise HTTPException(
                    status_code=400,
                    detail="Changing section to 'contextual' requires force_contextual=true"
                )
            if effective_section in {"useful", "harmful"} and request.updates.get("context"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Context must be empty for '{effective_section}' entries"
                )

            try:
                updated = exp_repo.update(request.entry_id, dict(request.updates))
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

            entry_dict = {
                "id": updated.id,
                "title": updated.title,
                "playbook": updated.playbook,
                "context": normalize_context(updated.context),
                "section": updated.section,
                "embedding_status": getattr(updated, "embedding_status", None),
                "updated_at": updated.updated_at,
                "author": updated.author,
                "source": updated.source,
                "sync_status": updated.sync_status,
            }

            return UpdateEntryResponse(
                success=True,
                entry_id=updated.id,
                entry=entry_dict,
                message=(
                    "Experience updated successfully. Indexing is in progress and may take up to 15 seconds. "
                    "Semantic search will not reflect this change until indexing is complete."
                ),
            )

        else:  # manual
            allowed_fields = {"title", "content", "summary"}
            if not request.updates:
                raise HTTPException(status_code=400, detail="No updates provided")
            invalid = set(request.updates.keys()) - allowed_fields
            if invalid:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported update fields: {', '.join(sorted(invalid))}"
                )

            man_repo = CategoryManualRepository(session)
            try:
                updated = man_repo.update(request.entry_id, dict(request.updates))
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

            manual_dict = {
                "id": updated.id,
                "title": updated.title,
                "content": updated.content,
                "summary": updated.summary,
                "embedding_status": getattr(updated, "embedding_status", None),
                "updated_at": updated.updated_at,
                "author": updated.author,
            }

            return UpdateEntryResponse(
                success=True,
                entry_id=updated.id,
                entry=manual_dict,
                message=(
                    "Manual updated successfully. Indexing is in progress and may take up to 15 seconds. "
                    "Semantic search will not reflect this change until indexing is complete."
                ),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating entry")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export")
def export_entries(
    session: Session = Depends(get_db_session),
) -> Dict[str, Any]:
    """Export all entries for Sheets/backup clients.

    Returns all experiences, manuals, and categories in a format suitable
    for exporting to Google Sheets or other external systems.
    """
    try:
        from src.common.storage.schema import Experience, CategoryManual, Category

        # Fetch all data
        experiences = session.query(Experience).all()
        manuals = session.query(CategoryManual).all()
        categories = session.query(Category).all()

        # Serialize to dicts
        experiences_data = []
        for exp in experiences:
            experiences_data.append({
                "id": exp.id,
                "category_code": exp.category_code,
                "section": exp.section,
                "title": exp.title,
                "playbook": exp.playbook,
                "context": exp.context,
                "source": exp.source,
                "sync_status": exp.sync_status,
                "author": exp.author,
                "embedding_status": exp.embedding_status,
                "created_at": exp.created_at,
                "updated_at": exp.updated_at,
                "synced_at": exp.synced_at,
                "exported_at": exp.exported_at,
            })

        manuals_data = []
        for man in manuals:
            manuals_data.append({
                "id": man.id,
                "category_code": man.category_code,
                "title": man.title,
                "content": man.content,
                "summary": man.summary,
                "source": man.source,
                "sync_status": man.sync_status,
                "author": man.author,
                "embedding_status": man.embedding_status,
                "created_at": man.created_at,
                "updated_at": man.updated_at,
                "synced_at": man.synced_at,
                "exported_at": man.exported_at,
            })

        categories_data = []
        for cat in categories:
            categories_data.append({
                "code": cat.code,
                "name": cat.name,
                "description": cat.description,
                "created_at": cat.created_at,
            })

        return {
            "experiences": experiences_data,
            "manuals": manuals_data,
            "categories": categories_data,
            "count": {
                "experiences": len(experiences_data),
                "manuals": len(manuals_data),
                "categories": len(categories_data),
            }
        }

    except Exception as e:
        logger.exception("Error exporting entries")
        raise HTTPException(status_code=500, detail=str(e))
