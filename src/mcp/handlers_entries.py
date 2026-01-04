"""MCP tool handlers for category listing and entry read/write operations."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.mcp.errors import MCPError
from src.mcp.core import get_cached_categories, set_categories_cache, request_api, config as runtime_config


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
    category_code: Optional[str] = None,
    query: Optional[str] = None,
    ids: Optional[List[str]] = None,
    limit: Optional[int] = None,
    fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Retrieve experiences or skills.

    USAGE PATTERNS:

    **1. Category-scoped (preferred when the shelf is clear):**
    - Small category (<20): `read_entries(entity_type='experience', category_code='PGS', fields=['playbook'])`
    - Large category (>=20): Load previews first (`read_entries(entity_type='experience', category_code='PGS')` for id/title/snippet), then fetch the selected IDs with fields=['playbook'] using the ID lookup call below.
        - ID lookup (global): `read_entries(entity_type='experience', ids=['EXP-PGS-xxx', 'EXP-DSD-yyy'])` — no category_code needed (IDs carry the prefix).

    **2. Global search (only when category is unclear or cross-category):**
    - `read_entries(entity_type='experience', query='[SEARCH] ... [TASK] ...')`
    - Omit category_code to search all categories.

    Defaults: responses return previews unless you request body fields (e.g., fields=['playbook'] or ['content']); default limit is the server's read_details_limit (10). Listing everything with no category_code is blocked to avoid huge responses.
    """
    try:
        if entity_type == "skill" and not getattr(runtime_config, "skills_enabled", True):
            raise MCPError("Skills are disabled in this installation.")
        payload: Dict[str, Any] = {
            "entity_type": entity_type,
        }
        if category_code is not None:
            payload["category_code"] = category_code
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
                else:  # skill
                    # Skills: id, name, description, content (or content_preview if not full)
                    filtered = {
                        "id": entry.get("id"),
                        "name": entry.get("name"),
                        "description": entry.get("description"),
                    }
                    if "content" in entry:
                        filtered["content"] = entry["content"]
                    elif "content_preview" in entry:
                        filtered["content_preview"] = entry["content_preview"]
                        filtered["content_truncated"] = entry.get("content_truncated", False)
                    for key in ("license", "compatibility", "metadata", "allowed_tools", "model"):
                        if key in entry:
                            filtered[key] = entry.get(key)
                    filtered_entries.append(filtered)

            response["entries"] = filtered_entries

        return response
    except MCPError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise MCPError(f"Unexpected error: {exc}") from exc


def create_entry(
    entity_type: str,
    category_code: str,
    data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Create a new experience or skill entry via the API.

    Args:
        entity_type: Either 'experience' or 'skill'
        category_code: Category shelf code (e.g., 'PGS', 'GLN')
        data: Entry payload

    CRITICAL - Atomicity Rule for Experiences:
        Each experience MUST be ATOMIC (one single technique/action/step).

        ✅ ATOMIC (correct):
          - "Use git rebase for clean history on feature branches"
          - "Set timeout to 30s for slow API endpoints"
          - "Add index on user_id for faster lookups"

        ❌ NON-ATOMIC (reject - split into multiple experiences):
          - "Set up authentication: configure OAuth, add middleware, and test login"
            → Split into: (1) "Configure OAuth provider", (2) "Add auth middleware", (3) "Test login flow"
          - "Optimize database: add indexes, enable query cache, and upgrade to v2"
            → Split into 3 separate atomic experiences

        Before creating an experience, verify:
          1. Does the playbook describe exactly ONE technique/action?
          2. Can it be applied independently without requiring other steps?
          3. Would splitting it into smaller pieces still make sense?

        If ANY answer is "no" or "maybe", SPLIT it into multiple atomic experiences.
        Each atomic piece should be self-contained and independently reusable.
    """
    try:
        if entity_type == "skill" and not getattr(runtime_config, "skills_enabled", True):
            raise MCPError("Skills are disabled in this installation.")
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
    Update an existing experience or skill entry by id.

    Allowed fields:
        - experience: title, playbook, context, section (use force_contextual=true to set section='contextual')
        - skill: name, description, content, license, compatibility, metadata, allowed_tools, model
    """
    try:
        if entity_type == "skill" and not getattr(runtime_config, "skills_enabled", True):
            raise MCPError("Skills are disabled in this installation.")
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
        entity_type: 'experience' or 'skill'
        category_code: Category shelf code (e.g., 'PGS')
        title: Proposed entry title
        content: Proposed playbook/content body
        limit: Maximum number of candidates to return (default: 1)
        threshold: Optional minimum similarity score (provider-specific)

    Note: Duplicate detection requires vector search. In CPU-only mode the API
    returns an empty list with a warning; callers should fall back to loading
    entries with read_entries and manually checking for overlap.
    """
    try:
        if entity_type == "skill" and not getattr(runtime_config, "skills_enabled", True):
            raise MCPError("Skills are disabled in this installation.")
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


__all__ = ["list_categories", "read_entries", "create_entry", "update_entry", "check_duplicates"]
