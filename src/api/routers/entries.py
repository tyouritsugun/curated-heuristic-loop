"""Entry endpoints for experiences and skills."""

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
import logging
import re

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
    CategorySkillRepository,
)
from src.common.dto.models import (
    ExperienceWritePayload,
    SkillWritePayload,
    format_validation_error,
    normalize_context,
)
from src.common.config.categories import get_categories
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

    Automatically tracks viewed entry IDs in session store when
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

        limit = request.limit if request.limit is not None else (config.read_details_limit if config else 100)

        # Determine snippet length (default 320, or from request)
        snippet_len = request.snippet_len if request.snippet_len is not None else 320
        use_preview = _should_use_preview(request.fields)

        if request.entity_type not in {"experience", "skill"}:
            raise HTTPException(status_code=400, detail="Unsupported entity_type")
        if request.entity_type == "skill" and not getattr(config, "skills_enabled", True):
            raise HTTPException(status_code=404, detail="Skills are disabled")

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

        elif request.entity_type == "skill":
            skill_repo = CategorySkillRepository(session)

            if request.query:
                # Semantic search for skills
                if search_service is None:
                    raise HTTPException(status_code=503, detail="Search service not initialized")

                results = search_service.search(
                    session=session,
                    query=request.query,
                    entity_type='skill',
                    category_code=request.category_code,
                    top_k=limit,
                )

                entries = []
                for r in results:
                    man = skill_repo.get_by_id(r.entity_id)
                    if not man:
                        continue

                    # Use new snippet generation for v1.1
                    preview, truncated = generate_snippet(man.content, max_length=snippet_len)

                    entry = {
                        "id": man.id,
                        "name": man.name,
                        "description": man.description,
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
                        if "license" in request.fields:
                            entry["license"] = man.license
                        if "compatibility" in request.fields:
                            entry["compatibility"] = man.compatibility
                        if "metadata" in request.fields:
                            entry["metadata"] = man.metadata_json
                        if "allowed_tools" in request.fields:
                            entry["allowed_tools"] = man.allowed_tools
                        if "model" in request.fields:
                            entry["model"] = man.model

                    entries.append(entry)
            else:
                # ID lookup or list all
                if request.ids:
                    # ID lookup works globally (IDs contain category prefix)
                    entities = [skill_repo.get_by_id(i) for i in request.ids]
                    entities = [e for e in entities if e is not None]
                else:
                    # List all requires category_code
                    if request.category_code is None:
                        raise HTTPException(
                            status_code=400,
                            detail="category_code required to list all entries (use query parameter for global search)"
                        )
                    all_mans = skill_repo.get_by_category(request.category_code)
                    entities = all_mans[:limit]

                entries = []
                for man in entities:
                    # Generate preview
                    preview, truncated = generate_snippet(man.content, max_length=snippet_len)

                    entry = {
                        "id": man.id,
                        "name": man.name,
                        "description": man.description,
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
                        if "license" in request.fields:
                            entry["license"] = man.license
                        if "compatibility" in request.fields:
                            entry["compatibility"] = man.compatibility
                        if "metadata" in request.fields:
                            entry["metadata"] = man.metadata_json
                        if "allowed_tools" in request.fields:
                            entry["allowed_tools"] = man.allowed_tools
                        if "model" in request.fields:
                            entry["model"] = man.model

                    entries.append(entry)

        else:
            raise HTTPException(status_code=400, detail="Unsupported entity_type")

        # Track viewed entries in session store
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
def create_entry(
    request: WriteEntryRequest,
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
    config=Depends(get_config),
):
    """Create a new entry.

    Automatically runs duplicate check with 750ms timeout.
    Decision tree:
    - Timeout → proceed with warning
    - Max score ≥ 0.85 → write, return duplicates + recommendation="review_first"
    - 0.50-0.84 → write, return duplicates as FYI
    - <0.50 → write normally
    """
    try:
        if request.entity_type == "skill" and not getattr(config, "skills_enabled", True):
            raise HTTPException(status_code=404, detail="Skills are disabled")
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

            # Auto-run duplicate check with hard 750ms timeout
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

            # Apply decision tree based on duplicate check results
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

        elif request.entity_type == "skill":
            # Validate skill payload
            try:
                validated = SkillWritePayload.model_validate({**request.data})
            except PydanticValidationError as exc:
                raise HTTPException(status_code=400, detail=format_validation_error(exc))
            name = validated.name
            description = validated.description
            content = validated.content

            cat_repo = CategoryRepository(session)
            category = cat_repo.get_by_code(request.category_code)
            if not category:
                raise HTTPException(
                    status_code=404,
                    detail=f"Category '{request.category_code}' not found"
                )

            # Auto-run duplicate check for skills with hard 750ms timeout
            import threading
            duplicate_candidates = []
            duplicate_check_timeout = False

            if search_service is not None:
                # Run duplicate check with hard timeout using threading
                result_container = {"candidates": None, "error": None}

                def run_duplicate_check():
                    try:
                        duplicate_title = name
                        duplicate_content = f"{description}\n\n{content}".strip()
                        result_container["candidates"] = search_service.find_duplicates(
                            session=session,
                            title=duplicate_title,
                            content=duplicate_content,
                            entity_type="skill",
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

            skill_repo = CategorySkillRepository(session)
            new_skill = skill_repo.create({
                "category_code": request.category_code,
                "name": name,
                "description": description,
                "content": content,
                "license": validated.license,
                "compatibility": validated.compatibility,
                "metadata": validated.metadata,
                "allowed_tools": validated.allowed_tools,
                "model": validated.model,
            })
            skill_id = new_skill.id

            skill_dict = {
                "id": new_skill.id,
                "name": new_skill.name,
                "description": new_skill.description,
                "content": new_skill.content,
                "license": new_skill.license,
                "compatibility": new_skill.compatibility,
                "metadata": new_skill.metadata_json,
                "allowed_tools": new_skill.allowed_tools,
                "model": new_skill.model,
                "embedding_status": getattr(new_skill, "embedding_status", None),
                "updated_at": new_skill.updated_at,
                "author": new_skill.author,
            }

            # Apply decision tree for skills
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
                entry_id=skill_id,
                entry=skill_dict,
                duplicates=duplicates_response,
                recommendation=recommendation,
                warnings=warnings or None,
                message=(
                    "Skill created successfully. Indexing is in progress and may take up to 15 seconds. "
                    "Semantic search will not reflect this change until indexing is complete."
                ),
            )
        else:
            raise HTTPException(status_code=400, detail="Unsupported entity_type")

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
        if request.entity_type == "skill" and not getattr(config, "skills_enabled", True):
            raise HTTPException(status_code=404, detail="Skills are disabled")
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

            if request.updates.get("section") == "contextual" and not request.force_contextual:
                raise HTTPException(
                    status_code=400,
                    detail="Changing section to 'contextual' requires force_contextual=true"
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

        elif request.entity_type == "skill":
            allowed_fields = {
                "name",
                "description",
                "content",
                "license",
                "compatibility",
                "metadata",
                "allowed_tools",
                "model",
            }
            if not request.updates:
                raise HTTPException(status_code=400, detail="No updates provided")
            invalid = set(request.updates.keys()) - allowed_fields
            if invalid:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported update fields: {', '.join(sorted(invalid))}"
                )

            if "name" in request.updates:
                name_value = str(request.updates["name"]).strip()
                if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name_value):
                    raise HTTPException(
                        status_code=400,
                        detail="name must be lowercase kebab-case (a-z0-9, hyphens, no consecutive hyphens)",
                    )
                request.updates["name"] = name_value

            if "description" in request.updates:
                desc_value = str(request.updates["description"]).strip()
                if not (1 <= len(desc_value) <= 1024):
                    raise HTTPException(
                        status_code=400,
                        detail="description must be 1-1024 characters",
                    )
                request.updates["description"] = desc_value

            skill_repo = CategorySkillRepository(session)
            try:
                updated = skill_repo.update(request.entry_id, dict(request.updates))
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

            skill_dict = {
                "id": updated.id,
                "name": updated.name,
                "description": updated.description,
                "content": updated.content,
                "license": updated.license,
                "compatibility": updated.compatibility,
                "metadata": updated.metadata_json,
                "allowed_tools": updated.allowed_tools,
                "model": updated.model,
                "embedding_status": getattr(updated, "embedding_status", None),
                "updated_at": updated.updated_at,
                "author": updated.author,
            }

            return UpdateEntryResponse(
                success=True,
                entry_id=updated.id,
                entry=skill_dict,
                message=(
                    "Skill updated successfully. Indexing is in progress and may take up to 15 seconds. "
                    "Semantic search will not reflect this change until indexing is complete."
                ),
            )
        else:
            raise HTTPException(status_code=400, detail="Unsupported entity_type")

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating entry")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export")
def export_entries(
    session: Session = Depends(get_db_session),
    config=Depends(get_config),
) -> Dict[str, Any]:
    """Export all entries for Sheets/backup clients.

    Returns all experiences and skills in a format suitable
    for exporting to Google Sheets or other external systems.
    """
    try:
        from src.common.storage.schema import Experience, CategorySkill

        # Fetch all data
        experiences = session.query(Experience).all()
        skills = []
        if getattr(config, "skills_enabled", True):
            skills = session.query(CategorySkill).all()

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

        skills_data = []
        for skill in skills:
            skills_data.append({
                "id": skill.id,
                "category_code": skill.category_code,
                "name": skill.name,
                "description": skill.description,
                "content": skill.content,
                "license": skill.license,
                "compatibility": skill.compatibility,
                "metadata": skill.metadata_json,
                "allowed_tools": skill.allowed_tools,
                "model": skill.model,
                "source": skill.source,
                "sync_status": skill.sync_status,
                "author": skill.author,
                "embedding_status": skill.embedding_status,
                "created_at": skill.created_at,
                "updated_at": skill.updated_at,
                "synced_at": skill.synced_at,
                "exported_at": skill.exported_at,
            })

        return {
            "experiences": experiences_data,
            "skills": skills_data,
            "count": {
                "experiences": len(experiences_data),
                "skills": len(skills_data),
            }
        }

    except Exception as e:
        logger.exception("Error exporting entries")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/export-csv")
def export_entries_csv(
    session: Session = Depends(get_db_session),
    config=Depends(get_config),
    external_skills_target: str | None = None,
):
    """Export all entries as CSV files in a zip archive for team curation workflow.

    Returns a zip file named {username}_export.zip containing:
    - {username}/experiences.csv
    - {username}/skills.csv
    """
    import csv
    import io
    import json
    import tempfile
    import zipfile
    from pathlib import Path
    from fastapi.responses import StreamingResponse
    from src.common.storage.repository import get_author
    from src.common.storage.schema import Experience, CategorySkill
    from src.common.skills.skill_md import parse_skill_md_loose

    try:
        # Get username from system
        username = get_author() or "unknown"

        external_target = (external_skills_target or "").strip().lower()
        if external_target == "chatgpt":
            external_target = "codex"

        # Fetch all data
        experiences = session.query(Experience).all()
        skills = []
        if getattr(config, "skills_enabled", True):
            skills = session.query(CategorySkill).all()
        else:
            if external_target and external_target != "none":
                # Skills disabled: read from selected SKILLS.md folder (with SKILL.md fallback)
                def iter_skill_md_paths(base_dir: Path):
                    if not base_dir.exists():
                        return []
                    paths = []
                    for child in base_dir.iterdir():
                        if child.is_dir():
                            skills_md = child / "SKILLS.md"
                            skill_md = child / "SKILL.md"
                            if skills_md.is_file():
                                paths.append(skills_md)
                            elif skill_md.is_file():
                                paths.append(skill_md)
                    return paths

                if external_target == "codex":
                    candidates = [
                        Path.cwd() / ".codex" / "skills",
                        Path.home() / ".codex" / "skills",
                    ]
                else:
                    candidates = [
                        Path.cwd() / ".claude" / "skills",
                        Path.home() / ".claude" / "skills",
                    ]

                skills_by_name = {}
                for base_dir in candidates:
                    for skill_md in iter_skill_md_paths(base_dir):
                        try:
                            data = parse_skill_md_loose(skill_md, require_dir_match=True)
                        except Exception as exc:
                            logger.warning("Skipping SKILLS.md parse error (%s): %s", skill_md, exc)
                            continue
                        name = data.get("name")
                        if not name or name in skills_by_name:
                            continue
                        category_code = (data.get("category_code") or "").strip()
                        metadata = data.get("metadata") or {}
                        allowed_tools = data.get("allowed_tools") or []
                        source_tag = f"external_{external_target}"
                        skills_by_name[name] = {
                            "id": name,
                            "category_code": category_code,
                            "name": name,
                            "description": data.get("description") or "",
                            "content": data.get("content") or "",
                            "license": data.get("license") or "",
                            "compatibility": data.get("compatibility") or "",
                            "metadata": json.dumps(metadata, ensure_ascii=False) if metadata else "",
                            "allowed_tools": " ".join(allowed_tools) if allowed_tools else "",
                            "model": data.get("model") or "",
                            "source": source_tag,
                            "author": username,
                            "embedding_status": "",
                            "created_at": "",
                            "updated_at": "",
                            "synced_at": "",
                            "exported_at": "",
                        }
                skills = list(skills_by_name.values())

        # Create in-memory zip file
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            # Export experiences
            if experiences:
                csv_buffer = io.StringIO()
                writer = csv.DictWriter(csv_buffer, fieldnames=[
                    "id", "category_code", "section", "title", "playbook", "context",
                    "source", "author", "embedding_status",
                    "created_at", "updated_at", "synced_at", "exported_at"
                ])
                writer.writeheader()
                for exp in experiences:
                    writer.writerow({
                        "id": exp.id,
                        "category_code": exp.category_code,
                        "section": exp.section,
                        "title": exp.title,
                        "playbook": exp.playbook,
                        "context": exp.context or "",
                        "source": exp.source or "",
                        "author": exp.author or "",
                        "embedding_status": exp.embedding_status or "",
                        "created_at": exp.created_at.isoformat() if exp.created_at else "",
                        "updated_at": exp.updated_at.isoformat() if exp.updated_at else "",
                        "synced_at": exp.synced_at.isoformat() if exp.synced_at else "",
                        "exported_at": exp.exported_at.isoformat() if exp.exported_at else "",
                    })
                zip_file.writestr(f"{username}/experiences.csv", csv_buffer.getvalue())

            # Export skills
            if skills:
                csv_buffer = io.StringIO()
                writer = csv.DictWriter(csv_buffer, fieldnames=[
                    "id",
                    "category_code",
                    "name",
                    "description",
                    "content",
                    "license",
                    "compatibility",
                    "metadata",
                    "allowed_tools",
                    "model",
                    "source", "author", "embedding_status",
                    "created_at", "updated_at", "synced_at", "exported_at"
                ])
                writer.writeheader()
                if getattr(config, "skills_enabled", True):
                    for skill in skills:
                        writer.writerow({
                            "id": skill.id,
                            "category_code": skill.category_code,
                            "name": skill.name,
                            "description": skill.description,
                            "content": skill.content,
                            "license": skill.license or "",
                            "compatibility": skill.compatibility or "",
                            "metadata": skill.metadata_json or "",
                            "allowed_tools": skill.allowed_tools or "",
                            "model": skill.model or "",
                            "source": skill.source or "",
                            "author": skill.author or "",
                            "embedding_status": skill.embedding_status or "",
                            "created_at": skill.created_at.isoformat() if skill.created_at else "",
                            "updated_at": skill.updated_at.isoformat() if skill.updated_at else "",
                            "synced_at": skill.synced_at.isoformat() if skill.synced_at else "",
                            "exported_at": skill.exported_at.isoformat() if skill.exported_at else "",
                        })
                else:
                    for skill in skills:
                        writer.writerow(skill)
                if getattr(config, "skills_enabled", True):
                    for skill in skills:
                        writer.writerow({
                            "id": skill.id,
                            "category_code": skill.category_code,
                            "name": skill.name,
                            "description": skill.description,
                            "content": skill.content,
                            "license": skill.license or "",
                            "compatibility": skill.compatibility or "",
                            "metadata": skill.metadata_json or "",
                            "allowed_tools": skill.allowed_tools or "",
                            "model": skill.model or "",
                            "source": skill.source or "",
                            "author": skill.author or "",
                            "embedding_status": skill.embedding_status or "",
                            "created_at": skill.created_at.isoformat() if skill.created_at else "",
                            "updated_at": skill.updated_at.isoformat() if skill.updated_at else "",
                            "synced_at": skill.synced_at.isoformat() if skill.synced_at else "",
                            "exported_at": skill.exported_at.isoformat() if skill.exported_at else "",
                        })
                else:
                    for skill in skills:
                        writer.writerow(skill)
                zip_file.writestr(f"{username}/skills.csv", csv_buffer.getvalue())

        # Prepare zip for download
        zip_buffer.seek(0)
        filename = f"{username}_export.zip"
        filename = f"{username}_export.zip"

        logger.info(
            "CSV export created for user=%s: %d experiences, %d skills",
            username,
            len(experiences),
            len(skills),
        )

        return StreamingResponse(
            zip_buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename=\"{filename}\""}
        )

    except Exception as e:
        logger.exception("Error exporting entries to CSV")
        raise HTTPException(status_code=500, detail=str(e))
