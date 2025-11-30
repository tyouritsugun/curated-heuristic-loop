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
    fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Retrieve experiences or manuals from a category.

    USAGE PATTERNS (choose based on category size from list_categories):

    **If category is small (total_count < 20):**
    1. LIST ALL WITH FULL CONTENT - Load everything at once
       - Example: read_entries(entity_type='experience', category_code='PGS', fields=['playbook'])
       - Returns: Full content for all entries (complete knowledge base in one call)

    **If category is large (total_count >= 20):**
    1. LIST PREVIEWS FIRST - Get titles/snippets + IDs
       - Example: read_entries(entity_type='experience', category_code='PGS')
       - Returns: Previews only (playbook_preview, truncated)

    2. RETRIEVE FULL CONTENT BY IDS - Fetch details for relevant entries
       - Example: read_entries(entity_type='experience', category_code='PGS',
                               ids=['EXP-PGS-xxx', 'EXP-PGS-yyy'], fields=['playbook'])
       - Returns: Full content for specified entries only

    3. SEARCH BY QUERY (lowest priority) - Find specific patterns if needed
       - Example: read_entries(entity_type='experience', category_code='PGS',
                               query='[SEARCH] handoff checklist [TASK] I need handoff process')
       - Returns: Semantically ranked results with previews

    RECOMMENDED WORKFLOW:
    1. If haven't yet, call list_categories() to see entry counts
    2. If small category: Load all with full content in one call
    3. If large category: Load previews → pick relevant IDs → fetch full content
    4. Use search only when you need specific patterns
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
        if fields is not None:
            payload["fields"] = fields

        response = request_api("POST", "/api/v1/entries/read", payload=payload)

        # Optimize response for LLMs - keep only essential fields to save tokens
        if "entries" in response:
            filtered_entries = []
            for entry in response["entries"]:
                if entity_type == "experience":
                    # Experiences: id, section, title, playbook (or playbook_preview if not full)
                    filtered = {"id": entry.get("id"), "section": entry.get("section"), "title": entry.get("title")}
                    if "playbook" in entry:
                        filtered["playbook"] = entry["playbook"]
                    elif "playbook_preview" in entry:
                        filtered["playbook_preview"] = entry["playbook_preview"]
                        filtered["playbook_truncated"] = entry.get("playbook_truncated", False)
                    filtered_entries.append(filtered)
                else:  # manual
                    # Manuals: id, title, content (or content_preview if not full)
                    filtered = {"id": entry.get("id"), "title": entry.get("title")}
                    if "content" in entry:
                        filtered["content"] = entry["content"]
                    elif "content_preview" in entry:
                        filtered["content_preview"] = entry["content_preview"]
                        filtered["content_truncated"] = entry.get("content_truncated", False)
                    filtered_entries.append(filtered)

            response["entries"] = filtered_entries

        return response
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


def check_duplicates(
    entity_type: str,
    category_code: str,
    title: str,
    content: str,
    limit: int = 1,
    threshold: float | None = None,
) -> Dict[str, Any]:
    """
    Check for potential duplicate entries before writing.

    Args:
        entity_type: 'experience' or 'manual'
        category_code: Category shelf code (e.g., 'PGS')
        title: Proposed entry title
        content: Proposed playbook/content body
        limit: Maximum number of candidates to return (default: 1)
        threshold: Optional minimum similarity score (provider-specific)
    """
    try:
        payload: Dict[str, Any] = {
            "entity_type": entity_type,
            "category_code": category_code,
            "title": title,
            "content": content,
            "limit": limit,
        }
        if threshold is not None:
            payload["threshold"] = threshold
        return request_api("POST", "/api/v1/search/duplicates", payload=payload)
    except MCPError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise MCPError(f"Unexpected error: {exc}") from exc


__all__ = ["list_categories", "read_entries", "write_entry", "update_entry", "check_duplicates"]
