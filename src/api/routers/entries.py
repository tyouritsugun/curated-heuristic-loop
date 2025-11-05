"""Entry endpoints for experiences and manuals."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Any
import logging

from src.api.dependencies import get_db_session, get_search_service, get_config
from src.api.models import (
    ReadEntriesRequest,
    ReadEntriesResponse,
    WriteEntryRequest,
    WriteEntryResponse,
    UpdateEntryRequest,
    UpdateEntryResponse,
    DeleteEntryRequest,
    DeleteEntryResponse,
)
from src.storage.repository import (
    CategoryRepository,
    ExperienceRepository,
    CategoryManualRepository,
)
from src.embedding.service import EmbeddingService
from src.mcp.models import ExperienceWritePayload, format_validation_error
from src.mcp.utils import normalize_context
from pydantic import ValidationError as PydanticValidationError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/entries", tags=["entries"])


def _make_preview(text: str | None, limit: int = 320) -> tuple[str | None, bool]:
    """Return a truncated preview and whether truncation occurred."""
    if text is None:
        return None, False
    trimmed = text.strip()
    if len(trimmed) <= limit:
        return trimmed, False
    return trimmed[:limit].rstrip() + "...", True


@router.post("/read", response_model=ReadEntriesResponse)
def read_entries(
    request: ReadEntriesRequest,
    session: Session = Depends(get_db_session),
    search_service=Depends(get_search_service),
    config=Depends(get_config),
):
    """Read entries by query or IDs."""
    try:
        # Validate category exists
        cat_repo = CategoryRepository(session)
        category = cat_repo.get_by_code(request.category_code)
        if not category:
            raise HTTPException(
                status_code=404,
                detail=f"Category '{request.category_code}' not found"
            )

        limit = request.limit if request.limit is not None else (config.read_details_limit if config else 10)

        if request.entity_type == "experience":
            exp_repo = ExperienceRepository(session)

            if request.query:
                # Semantic search
                if search_service is None:
                    raise HTTPException(status_code=503, detail="Search service not initialized")

                results = search_service.search(
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
                    preview, truncated = _make_preview(exp.playbook)
                    entries.append({
                        "id": exp.id,
                        "title": exp.title,
                        "playbook": preview,
                        "playbook_preview": preview,
                        "playbook_truncated": truncated,
                        "context": normalize_context(exp.context),
                        "section": exp.section,
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
                    })
            else:
                # ID lookup or list all
                if request.ids:
                    entities = [exp_repo.get_by_id(i) for i in request.ids]
                    entities = [e for e in entities if e is not None]
                else:
                    all_exps = exp_repo.get_by_category(request.category_code)
                    entities = all_exps[:limit]

                entries = []
                for exp in entities:
                    entries.append({
                        "id": exp.id,
                        "title": exp.title,
                        "playbook": exp.playbook,
                        "context": normalize_context(exp.context),
                        "section": exp.section,
                        "updated_at": exp.updated_at,
                        "author": exp.author,
                        "source": exp.source,
                        "sync_status": exp.sync_status,
                        "reason": "id_lookup",
                        "provider": "direct",
                    })

        else:  # manual
            man_repo = CategoryManualRepository(session)

            if request.query:
                # Semantic search for manuals
                if search_service is None:
                    raise HTTPException(status_code=503, detail="Search service not initialized")

                results = search_service.search(
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
                    preview, truncated = _make_preview(man.content)
                    entries.append({
                        "id": man.id,
                        "title": man.title,
                        "content": preview,
                        "content_preview": preview,
                        "content_truncated": truncated,
                        "summary": man.summary,
                        "updated_at": man.updated_at,
                        "author": man.author,
                        "score": r.score,
                        "reason": getattr(r.reason, 'value', str(r.reason)),
                        "provider": r.provider,
                        "rank": r.rank,
                    })
            else:
                # ID lookup or list all
                if request.ids:
                    entities = [man_repo.get_by_id(i) for i in request.ids]
                    entities = [e for e in entities if e is not None]
                else:
                    all_mans = man_repo.get_by_category(request.category_code)
                    entities = all_mans[:limit]

                entries = []
                for man in entities:
                    entries.append({
                        "id": man.id,
                        "title": man.title,
                        "content": man.content,
                        "summary": man.summary,
                        "updated_at": man.updated_at,
                        "author": man.author,
                        "reason": "id_lookup",
                        "provider": "direct",
                    })

        return ReadEntriesResponse(entries=entries, count=len(entries))

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
    """Create a new entry."""
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
            # Validate experience data
            try:
                validated = ExperienceWritePayload.model_validate({**request.data})
            except PydanticValidationError as e:
                raise HTTPException(status_code=400, detail=format_validation_error(e))

            exp_repo = ExperienceRepository(session)
            new_obj = exp_repo.create({
                "category_code": request.category_code,
                "section": validated.section,
                "title": validated.title,
                "playbook": validated.playbook,
                "context": validated.context,
            })
            entry_id = new_obj.id

            # Best-effort embedding after commit
            try:
                if getattr(config, "embed_on_write", False) and search_service is not None:
                    vp = getattr(search_service, "get_vector_provider", lambda: None)()
                    if vp and hasattr(vp, "embedding_client") and hasattr(vp, "index_manager"):
                        emb = EmbeddingService(
                            session=session,
                            embedding_client=vp.embedding_client,
                            faiss_index_manager=vp.index_manager
                        )
                        emb.upsert_for_experience(entry_id)
            except Exception:
                logger.exception(f"Inline embedding failed for experience {entry_id}")

            return WriteEntryResponse(
                success=True,
                entry_id=entry_id,
                message="Experience created successfully"
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

            man_repo = CategoryManualRepository(session)
            new_manual = man_repo.create({
                "category_code": request.category_code,
                "title": title,
                "content": content,
                "summary": summary,
            })
            manual_id = new_manual.id

            # Inline embedding optional
            try:
                if getattr(config, "embed_on_write", False) and search_service is not None:
                    vp = getattr(search_service, "get_vector_provider", lambda: None)()
                    if vp and hasattr(vp, "embedding_client") and hasattr(vp, "index_manager"):
                        emb = EmbeddingService(
                            session=session,
                            embedding_client=vp.embedding_client,
                            faiss_index_manager=vp.index_manager
                        )
                        emb.upsert_for_manual(manual_id)
            except Exception:
                logger.exception(f"Inline embedding failed for manual {manual_id}")

            return WriteEntryResponse(
                success=True,
                entry_id=manual_id,
                message="Manual created successfully"
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

            # Inline embedding update
            try:
                if getattr(config, "embed_on_write", False) and search_service is not None:
                    vp = getattr(search_service, "get_vector_provider", lambda: None)()
                    if vp and hasattr(vp, "embedding_client") and hasattr(vp, "index_manager"):
                        emb = EmbeddingService(
                            session=session,
                            embedding_client=vp.embedding_client,
                            faiss_index_manager=vp.index_manager
                        )
                        emb.upsert_for_experience(request.entry_id)
            except Exception:
                logger.exception(f"Inline embedding update failed for experience {request.entry_id}")

            return UpdateEntryResponse(
                success=True,
                entry_id=updated.id,
                message="Experience updated successfully"
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

            try:
                if getattr(config, "embed_on_write", False) and search_service is not None:
                    vp = getattr(search_service, "get_vector_provider", lambda: None)()
                    if vp and hasattr(vp, "embedding_client") and hasattr(vp, "index_manager"):
                        emb = EmbeddingService(
                            session=session,
                            embedding_client=vp.embedding_client,
                            faiss_index_manager=vp.index_manager
                        )
                        emb.upsert_for_manual(request.entry_id)
            except Exception:
                logger.exception(f"Inline embedding update failed for manual {request.entry_id}")

            return UpdateEntryResponse(
                success=True,
                entry_id=updated.id,
                message="Manual updated successfully"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating entry")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete", response_model=DeleteEntryResponse)
def delete_entry(
    request: DeleteEntryRequest,
    session: Session = Depends(get_db_session),
):
    """Delete an entry (manuals only)."""
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
            raise HTTPException(
                status_code=400,
                detail="Delete is not supported for experiences"
            )

        # Manual delete
        man_repo = CategoryManualRepository(session)
        manual = man_repo.get_by_id(request.entry_id)
        if not manual:
            raise HTTPException(
                status_code=404,
                detail=f"Manual '{request.entry_id}' not found"
            )
        if manual.category_code != request.category_code:
            raise HTTPException(
                status_code=400,
                detail=f"Manual '{request.entry_id}' belongs to category '{manual.category_code}', not '{request.category_code}'"
            )

        man_repo.delete(request.entry_id)
        return DeleteEntryResponse(
            success=True,
            entry_id=request.entry_id,
            message="Manual deleted successfully"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error deleting entry")
        raise HTTPException(status_code=500, detail=str(e))
