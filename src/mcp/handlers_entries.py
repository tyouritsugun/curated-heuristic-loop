"""Unified handlers for both experiences and manuals.

Tools exposed:
- read_entries(entity_type, category_code, ids?, limit?, query?)
- write_entry(entity_type, category_code, data)
- update_entry(entity_type, category_code, entry_id, updates, force_contextual?)
- delete_entry(entity_type, category_code, entry_id)

All functions return standardized envelopes with { meta: {code,name}, ... } or
create_error_response(...) on failure.
"""
from typing import Any, Dict, List, Optional, Literal
import logging

from pydantic import ValidationError

from src.mcp.models import ExperienceWritePayload, format_validation_error
from src.mcp.utils import create_error_response, normalize_context
from src.storage.repository import (
    CategoryRepository,
    ExperienceRepository,
    CategoryManualRepository,
)

logger = logging.getLogger(__name__)

EntityType = Literal["experience", "manual"]


def _validate_entity_type(entity_type: str) -> Optional[str]:
    if entity_type not in ("experience", "manual"):
        return f"Unknown entity_type '{entity_type}'. Use 'experience' or 'manual'."
    return None


def _make_preview(text: Optional[str], limit: int = 320) -> tuple[Optional[str], bool]:
    """Return a truncated preview and whether truncation occurred."""
    if text is None:
        return None, False
    trimmed = text.strip()
    if len(trimmed) <= limit:
        return trimmed, False
    return trimmed[:limit].rstrip() + "...", True



def make_read_entries_handler(db, config, search_service):
    def read_entries(
        entity_type: EntityType,
        category_code: str,
        ids: Optional[List[str]] = None,
        limit: Optional[int] = None,
        query: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            err = _validate_entity_type(entity_type)
            if err:
                return create_error_response("INVALID_REQUEST", err)
            if db is None:
                return create_error_response(
                    "SERVER_ERROR",
                    "Server not initialized",
                    hint="Call init_server() before invoking MCP tools.",
                    retryable=True,
                )
            meta_code = category_code
            meta_name = category_code
            if limit is None:
                limit = config.read_details_limit if config else 10

            with db.session_scope() as session:
                cat_repo = CategoryRepository(session)
                category = cat_repo.get_by_code(category_code)
                if not category:
                    return create_error_response(
                        "CATEGORY_NOT_FOUND",
                        f"Category '{category_code}' not found",
                        hint="Use list_categories to confirm the shelf code before querying.",
                        retryable=False,
                    )
                meta_code = category.code
                meta_name = category.name

                if entity_type == "experience":
                    exp_repo = ExperienceRepository(session)
                    if query:
                        if search_service is None:
                            return create_error_response(
                                "SERVER_ERROR",
                                "Search service not initialized",
                                hint="Restart the CHL server to register search providers.",
                                retryable=True,
                            )
                        results = search_service.search(
                            query=query,
                            entity_type='experience',
                            category_code=category_code,
                            top_k=limit,
                        )
                        entries: List[Dict[str, Any]] = []
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
                        if ids:
                            entities = [exp_repo.get_by_id(i) for i in ids]
                            entities = [e for e in entities if e is not None]
                        else:
                            all_exps = exp_repo.get_by_category(category_code)
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
                    if query:
                        if search_service is None:
                            return create_error_response(
                                "SERVER_ERROR",
                                "Search service not initialized",
                                hint="Restart the CHL server to register search providers.",
                                retryable=True,
                            )
                        results = search_service.search(
                            query=query,
                            entity_type='manual',
                            category_code=category_code,
                            top_k=limit,
                        )
                        entries = []
                        for r in results:
                            m = man_repo.get_by_id(r.entity_id)
                            if not m:
                                continue
                            preview, truncated = _make_preview(m.content, limit=480)
                            entries.append({
                                "id": m.id,
                                "title": m.title,
                                "content": preview,
                                "content_preview": preview,
                                "content_truncated": truncated,
                                "summary": m.summary,
                                "updated_at": m.updated_at,
                                "author": m.author,
                                "score": r.score,
                                "reason": getattr(r.reason, 'value', str(r.reason)),
                                "provider": r.provider,
                                "rank": r.rank,
                                "degraded": getattr(r, "degraded", False),
                                "provider_hint": getattr(r, "hint", None),
                            })
                    else:
                        if ids:
                            entities = [man_repo.get_by_id(i) for i in ids]
                            entities = [e for e in entities if e is not None]
                        else:
                            all_m = man_repo.get_by_category(category_code)
                            entities = all_m[:limit]
                        entries = []
                        for m in entities:
                            entries.append({
                                "id": m.id,
                                "title": m.title,
                                "content": m.content,
                                "summary": m.summary,
                                "updated_at": m.updated_at,
                                "author": m.author,
                                "reason": "id_lookup",
                                "provider": "direct",
                            })

            return {"meta": {"code": meta_code, "name": meta_name}, "entries": entries}
        except Exception as e:
            return create_error_response("SERVER_ERROR", str(e), retryable=False)

    read_entries.__doc__ = (
        "Retrieve experiences or manuals from a category by ids or semantic query.\n\n"
        "Example:\n"
        "    read_entries(entity_type='experience', category_code='PGS', query='handoff checklist')"
    )
    read_entries.__name__ = "read_entries"
    return read_entries


def make_write_entry_handler(db, config, search_service):
    def write_entry(
        entity_type: EntityType,
        category_code: str,
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            err = _validate_entity_type(entity_type)
            if err:
                return create_error_response("INVALID_REQUEST", err)
            if db is None:
                return create_error_response(
                    "SERVER_ERROR",
                    "Server not initialized",
                    hint="Call init_server() before invoking MCP tools.",
                    retryable=True,
                )

            with db.session_scope() as session:
                cat_repo = CategoryRepository(session)
                category = cat_repo.get_by_code(category_code)
                if not category:
                    return create_error_response(
                        "CATEGORY_NOT_FOUND",
                        f"Category '{category_code}' not found",
                        hint="Use list_categories to confirm the shelf code before writing.",
                        retryable=False,
                    )
                meta_code = category.code
                meta_name = category.name

                if entity_type == "experience":
                    warnings: List[str] = []
                    try:
                        validated = ExperienceWritePayload.model_validate({**data})
                    except ValidationError as e:
                        return create_error_response("INVALID_REQUEST", format_validation_error(e))

                    # Duplicate suggestions
                    duplicates_payload: List[Dict[str, Any]] = []
                    try:
                        if search_service is not None:
                            dup_candidates = search_service.find_duplicates(
                                title=validated.title,
                                content=validated.playbook,
                                entity_type='experience',
                                category_code=category_code,
                                exclude_id=None,
                                threshold=(config.duplicate_threshold_insert if config else 0.60),
                            )
                            for c in dup_candidates:
                                duplicates_payload.append({
                                    "entity_id": c.entity_id,
                                    "entity_type": c.entity_type,
                                    "score": c.score,
                                    "reason": getattr(c.reason, 'value', str(c.reason)),
                                    "provider": c.provider,
                                    "title": c.title,
                                    "summary": c.summary,
                                    "guidance": "Potential duplicate - compare before finalizing."
                                })
                    except Exception:
                        pass

                    raw_context = data.get("context")
                    if raw_context and validated.section in {"useful", "harmful"}:
                        warnings.append(
                            "Context was ignored because section='useful' or 'harmful'; use section='contextual' if you need context."
                        )

                    exp_repo = ExperienceRepository(session)
                    new_obj = exp_repo.create({
                        "category_code": category_code,
                        "section": validated.section,
                        "title": validated.title,
                        "playbook": validated.playbook,
                        "context": validated.context,
                    })
                    entry_id = new_obj.id

                    # Best-effort embedding after commit
                    response: Dict[str, Any] = {
                        "meta": {"code": meta_code, "name": meta_name},
                        "entry_id": entry_id,
                        "duplicates": duplicates_payload,
                    }
                    if warnings:
                        response["warnings"] = warnings
                    return response

                else:  # manual
                    # Basic validation
                    title = (data or {}).get("title")
                    content = (data or {}).get("content")
                    summary = (data or {}).get("summary")
                    if not title or len(title) > 120:
                        return create_error_response(
                            "INVALID_REQUEST",
                            "Title must be 1-120 characters",
                            hint="Shorten the title so it stays within 120 characters.",
                            retryable=True,
                        )
                    if not content:
                        return create_error_response(
                            "INVALID_REQUEST",
                            "Content cannot be empty",
                            hint="Provide Markdown content before creating the manual.",
                            retryable=True,
                        )

                    man_repo = CategoryManualRepository(session)
                    new_manual = man_repo.create({
                        "category_code": category_code,
                        "title": title,
                        "content": content,
                        "summary": summary,
                    })

                    manual_id = new_manual.id

                    return {"meta": {"code": meta_code, "name": meta_name}, "entry_id": manual_id}

        except Exception as e:
            return create_error_response("SERVER_ERROR", str(e), retryable=False)

    write_entry.__doc__ = (
        "Create a new experience or manual entry in the requested category.\n\n"
        "Example:\n"
        "    write_entry('experience', 'PGS', {\n"
        "        'section': 'useful',\n"
        "        'title': 'Checklist the spec handoff',\n"
        "        'playbook': 'Review the Figma comments before handoff.',\n"
        "        'context': {'note': 'Only used when section is contextual.'}\n"
        "    })"
    )
    write_entry.__name__ = "write_entry"
    return write_entry


def make_update_entry_handler(db, config, search_service):
    def update_entry(
        entity_type: EntityType,
        category_code: str,
        entry_id: str,
        updates: Dict[str, Any],
        force_contextual: bool = False,
    ) -> Dict[str, Any]:
        try:
            err = _validate_entity_type(entity_type)
            if err:
                return create_error_response("INVALID_REQUEST", err)
            if db is None:
                return create_error_response(
                    "SERVER_ERROR",
                    "Server not initialized",
                    hint="Call init_server() before invoking MCP tools.",
                    retryable=True,
                )

            with db.session_scope() as session:
                cat_repo = CategoryRepository(session)
                category = cat_repo.get_by_code(category_code)
                if not category:
                    return create_error_response(
                        "CATEGORY_NOT_FOUND",
                        f"Category '{category_code}' not found",
                        hint="Use list_categories to confirm the shelf code before updating.",
                        retryable=False,
                    )
                meta_code = category.code
                meta_name = category.name

                if entity_type == "experience":
                    allowed_fields = {"title", "playbook", "context", "section"}
                    invalid_fields = set(updates.keys()) - allowed_fields
                    if not updates or invalid_fields:
                        if not updates:
                            return create_error_response(
                                "INVALID_REQUEST",
                                "No updates provided",
                                hint="Supply at least one supported field to update.",
                                retryable=True,
                            )
                        return create_error_response(
                            "INVALID_REQUEST",
                            f"Unsupported update fields: {', '.join(sorted(invalid_fields))}",
                            hint="Allowed experience fields are title, playbook, context, and section.",
                            retryable=True,
                        )
                    exp_repo = ExperienceRepository(session)
                    existing = exp_repo.get_by_id(entry_id)
                    if not existing:
                        return create_error_response(
                            "INVALID_REQUEST",
                            f"Entry '{entry_id}' not found",
                            hint="Verify the entry_id or list recent experiences before updating.",
                            retryable=False,
                        )
                    effective_section = updates.get("section") or existing.section
                    if updates.get("section") == "contextual" and not force_contextual:
                        return create_error_response(
                            "INVALID_REQUEST",
                            "Changing section to 'contextual' requires force_contextual=true",
                            hint="Pass force_contextual=true when promoting an entry into the contextual section.",
                            retryable=True,
                        )
                    if effective_section in {"useful", "harmful"} and updates.get("context"):
                        return create_error_response(
                            "INVALID_REQUEST",
                            f"Context must be empty for '{effective_section}' entries",
                            hint="Move detailed context into a contextual entry or change the section type.",
                            retryable=True,
                        )
                    try:
                        updated = exp_repo.update(entry_id, dict(updates))
                    except ValueError as e:
                        return create_error_response("INVALID_REQUEST", str(e), retryable=True)

                    entry_dict = {
                        "id": updated.id,
                        "title": updated.title,
                        "playbook": updated.playbook,
                        "context": normalize_context(updated.context),
                        "section": updated.section,
                        "updated_at": updated.updated_at,
                        "author": updated.author,
                        "source": updated.source,
                        "sync_status": updated.sync_status,
                    }

                    return {"meta": {"code": meta_code, "name": meta_name}, "entry": entry_dict}

                else:  # manual
                    allowed_fields = {"title", "content", "summary"}
                    if not updates:
                        return create_error_response(
                            "INVALID_REQUEST",
                            "No updates provided",
                            hint="Supply at least one supported field to update.",
                            retryable=True,
                        )
                    invalid = set(updates.keys()) - allowed_fields
                    if invalid:
                        return create_error_response(
                            "INVALID_REQUEST",
                            f"Unsupported update fields: {', '.join(sorted(invalid))}",
                            hint="Allowed manual fields are title, content, and summary.",
                            retryable=True,
                        )
                    man_repo = CategoryManualRepository(session)
                    try:
                        updated = man_repo.update(entry_id, dict(updates))
                    except ValueError as e:
                        return create_error_response("INVALID_REQUEST", str(e), retryable=True)

                    manual_dict = {
                        "id": updated.id,
                        "title": updated.title,
                        "content": updated.content,
                        "summary": updated.summary,
                        "updated_at": updated.updated_at,
                        "author": updated.author,
                    }

                    return {"meta": {"code": meta_code, "name": meta_name}, "entry": manual_dict}

        except Exception as e:
            return create_error_response("SERVER_ERROR", str(e), retryable=False)

    update_entry.__doc__ = (
        "Update an existing experience or manual entry by id.\n\n"
        "Example:\n"
        "    update_entry('manual', 'PGS', 'MNL-PGS-20250115-104200123456', {\n"
        "        'summary': 'Adds audit checklist step.'\n"
        "    })"
    )
    update_entry.__name__ = "update_entry"
    return update_entry


def make_delete_entry_handler(db, _config, _search_service):
    def delete_entry(entity_type: EntityType, category_code: str, entry_id: str) -> Dict[str, Any]:
        try:
            err = _validate_entity_type(entity_type)
            if err:
                return create_error_response("INVALID_REQUEST", err)
            if db is None:
                return create_error_response(
                    "SERVER_ERROR",
                    "Server not initialized",
                    hint="Call init_server() before invoking MCP tools.",
                    retryable=True,
                )

            with db.session_scope() as session:
                cat_repo = CategoryRepository(session)
                category = cat_repo.get_by_code(category_code)
                if not category:
                    return create_error_response(
                        "CATEGORY_NOT_FOUND",
                        f"Category '{category_code}' not found",
                        hint="Use list_categories to confirm the shelf code before deleting.",
                        retryable=False,
                    )

                if entity_type == "experience":
                    return create_error_response(
                        "INVALID_REQUEST",
                        "Delete is not supported for experiences",
                        hint="Experiences are immutable; update the entry instead of deleting.",
                        retryable=False,
                    )

                # Manual delete
                man_repo = CategoryManualRepository(session)
                manual = man_repo.get_by_id(entry_id)
                if not manual:
                    return create_error_response(
                        "INVALID_REQUEST",
                        f"Manual '{entry_id}' not found",
                        hint="Verify the entry_id or list recent manuals before deleting.",
                        retryable=False,
                    )
                if manual.category_code != category_code:
                    return create_error_response(
                        "INVALID_REQUEST",
                        f"Manual '{entry_id}' belongs to category '{manual.category_code}', not '{category_code}'",
                        hint="Pass the matching category_code when deleting a manual.",
                        retryable=False,
                    )
                man_repo.delete(entry_id)
                return {"status": "deleted", "entry_id": entry_id}

        except Exception as e:
            return create_error_response("SERVER_ERROR", str(e), retryable=False)

    delete_entry.__doc__ = (
        "Delete a manual entry from a category (experiences cannot be deleted).\n\n"
        "Example:\n"
        "    delete_entry('manual', 'PGS', 'MNL-PGS-20250115-104200123456')"
    )
    delete_entry.__name__ = "delete_entry"
    return delete_entry
