"""MCP tool handlers for category listing and entry read/write operations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.mcp.errors import MCPError
from src.mcp.core import get_cached_categories, set_categories_cache, request_api


def list_categories() -> Dict[str, Any]:
    """
    Return the current set of available category shelves.

    Example:
        {}

    Returns:
        Dictionary with 'categories' list containing code and name for each category.
    """
    cached = get_cached_categories()
    if cached is not None:
        return cached

    try:
        payload = request_api("GET", "/api/v1/categories/")
        set_categories_cache(payload)
        return payload
    except MCPError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise MCPError(f"Unexpected error: {exc}") from exc


def read_entries(
    entity_type: str,
    category_code: str,
    query: Optional[str] = None,
    ids: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Retrieve experiences or manuals from a category by ids or semantic query.

    Example:
        read_entries(entity_type='experience', category_code='PGS', query='handoff checklist')
    """
    try:
        payload: Dict[str, Any] = {
            "entity_type": entity_type,
            "category_code": category_code,
        }
        if query is not None:
            payload["query"] = query
        if ids is not None:
            payload["ids"] = ids
        if limit is not None:
            payload["limit"] = limit

        return request_api("POST", "/api/v1/entries/read", payload=payload)
    except MCPError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise MCPError(f"Unexpected error: {exc}") from exc


def write_entry(
    entity_type: str,
    category_code: str,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Create a new experience or manual entry via the API.

    Args:
        entity_type: Either 'experience' or 'manual'
        category_code: Category shelf code (e.g., 'PGS', 'GLN')
        data: Entry payload
    """
    try:
        payload = {
            "entity_type": entity_type,
            "category_code": category_code,
            "data": data,
        }
        return request_api("POST", "/api/v1/entries/write", payload=payload)
    except MCPError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise MCPError(f"Unexpected error: {exc}") from exc


def update_entry(
    entity_type: str,
    category_code: str,
    entry_id: str,
    updates: Dict[str, Any],
    force_contextual: bool = False,
) -> Dict[str, Any]:
    """
    Update an existing experience or manual entry by id.

    Allowed fields:
        - experience: title, playbook, context, section (use force_contextual=true to set section='contextual')
        - manual: title, content, summary
    """
    try:
        payload = {
            "entity_type": entity_type,
            "category_code": category_code,
            "entry_id": entry_id,
            "updates": updates,
            "force_contextual": force_contextual,
        }
        return request_api("POST", "/api/v1/entries/update", payload=payload)
    except MCPError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise MCPError(f"Unexpected error: {exc}") from exc


__all__ = ["list_categories", "read_entries", "write_entry", "update_entry"]

