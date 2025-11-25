"""Shared MCP server runtime: HTTP client, caching, and diagnostics."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from copy import deepcopy
from typing import Any, Dict, Optional

from src.common.api_client.client import CHLAPIClient
from src.mcp.errors import MCPError, MCPTransportError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static MCP metadata
# ---------------------------------------------------------------------------

SERVER_VERSION = "1.1.0"

TOOL_INDEX = [
    {
        "name": "list_categories",
        "description": "List all available category shelves with code and name.",
        "example": {},
    },
    {
        "name": "read_entries",
        "description": "Fetch experiences or manuals by ids or semantic query.",
        "example": {
            "entity_type": "experience",
            "category_code": "PGS",
            "query": "handoff checklist",
        },
    },
    {
        "name": "write_entry",
        "description": "Create a new experience or manual in a category. Prefer calling check_duplicates first to inspect similar entries.",
        "example": {
            "entity_type": "experience",
            "category_code": "PGS",
            "data": {
                "section": "useful",
                "title": "Review breakpoints before spec",
                "playbook": "Confirm responsive states with design before writing HTML.",
            },
        },
    },
    {
        "name": "update_entry",
        "description": "Update an existing experience or manual by id.",
        "example": {
            "entity_type": "manual",
            "category_code": "PGS",
            "entry_id": "MNL-PGS-20250115-104200123456",
            "updates": {"summary": "Adds audit checklist step."},
        },
    },
    {
        "name": "get_guidelines",
        "description": "Return the generator or evaluator workflow manual seeded in GLN.",
        "example": {"guide_type": "generator"},
    },
    {
        "name": "check_duplicates",
        "description": "Check for potential duplicate entries before calling write_entry.",
        "example": {
            "entity_type": "experience",
            "category_code": "PGS",
            "title": "Baseline checklist before drafting a page spec",
            "content": "Full playbook text here",
            "limit": 1,
        },
    },
]

WORKFLOW_MODE_PAYLOAD = {
    "default": "generator",
    "notes": (
        "Sessions start in Generator mode. Load generator guidelines first and "
        "switch to evaluator deliberately when reflecting on completed work."
    ),
    "guidelines": {
        "generator": "Use guide_type='generator' to fetch the authoring manual.",
        "evaluator": "Use guide_type='evaluator' only after generator work is done.",
    },
}

# HTTP mode is always enabled - no CLI override needed
HTTP_MODE = "http"

# ---------------------------------------------------------------------------
# Global runtime (configured by src.mcp.server)
# ---------------------------------------------------------------------------

config: Any = None
api_client: Optional[CHLAPIClient] = None


def set_runtime(config_obj: Any, client: CHLAPIClient) -> None:
    """Attach Config and CHLAPIClient for use by handlers."""
    global config, api_client
    config = config_obj
    api_client = client


# ---------------------------------------------------------------------------
# Categories cache
# ---------------------------------------------------------------------------

try:
    CATEGORIES_CACHE_TTL = float(os.getenv("CHL_CATEGORIES_CACHE_TTL", "30.0"))
except (TypeError, ValueError):
    CATEGORIES_CACHE_TTL = 30.0

_categories_cache: Dict[str, Any] = {"payload": None, "expires": 0.0}
_categories_cache_lock = threading.Lock()


def workflow_mode_payload() -> Dict[str, Any]:
    """Return a copy of the workflow mode instructions."""
    return deepcopy(WORKFLOW_MODE_PAYLOAD)


def get_cached_categories() -> Optional[Dict[str, Any]]:
    """Return cached categories payload if still valid."""
    with _categories_cache_lock:
        payload = _categories_cache.get("payload")
        expires = _categories_cache.get("expires", 0.0)
        # Use monotonic time to avoid issues with system clock adjustments
        if payload is None or expires < time.monotonic():
            return None
        return payload


def set_categories_cache(payload: Dict[str, Any]) -> None:
    """Update categories cache with fresh payload."""
    with _categories_cache_lock:
        _categories_cache["payload"] = payload
        # Use monotonic time to avoid issues with system clock adjustments
        _categories_cache["expires"] = time.monotonic() + CATEGORIES_CACHE_TTL


def invalidate_categories_cache() -> None:
    """Clear categories cache, e.g., after settings changes."""
    with _categories_cache_lock:
        _categories_cache["payload"] = None
        _categories_cache["expires"] = 0.0


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def request_api(
    method: str,
    path: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Call API server via CHLAPIClient configured in set_runtime()."""
    if api_client is None:
        raise MCPError("HTTP client not initialized")

    request_kwargs: Dict[str, Any] = {"json": payload} if payload is not None else {}
    if headers:
        request_kwargs["headers"] = headers
    return api_client.request(method, path, **request_kwargs)


def build_handshake_payload() -> Dict[str, Any]:
    """Build startup instructions payload from API server."""
    if api_client is None:
        logger.error("API client not initialized")
        return {
            "version": SERVER_VERSION,
            "error": "API client not initialized",
            "tool_index": TOOL_INDEX,
        }

    try:
        categories_data = api_client.request("GET", "/api/v1/categories/")
        set_categories_cache(categories_data)

        health_data = api_client.get_health()
        faiss_status = health_data.get("components", {}).get("faiss_index", {}).get("status")
        vector_healthy = faiss_status == "healthy"

        search_payload: Dict[str, Any] = {
            "primary_provider": "vector_faiss" if vector_healthy else "sqlite_text",
            "vector_available": vector_healthy,
            "fallback_enabled": True,
            "status": health_data.get("status", "unknown"),
        }

        if health_data.get("status") == "degraded":
            search_payload["hint"] = (
                "Vector search disabled; responses use sqlite_text fallback. "
                "Install ML extras and rebuild embeddings to restore semantic search."
            )

        return {
            "version": SERVER_VERSION,
            "workflow_mode": workflow_mode_payload(),
            "tool_index": TOOL_INDEX,
            "search": search_payload,
            "categories": categories_data.get("categories", []),
            "mode": {
                "transport": HTTP_MODE,
                "base_url": getattr(config, "api_base_url", "unknown") if config else "unknown",
            },
            "instructions": {
                "task_clarification": (
                    "Clarify user's intent before taking action when they report bugs/errors. "
                    "They may want to: fix code, write a ticket (check TMG category), "
                    "investigate, or document. Don't assume they want an immediate code fix."
                ),
                "session_memory": {
                    "overview": (
                        "Phase 2: Session memory tracks viewed entries to avoid repetition. "
                        "Initialize a session once per conversation using CHLAPIClient(session_id=...), "
                        "or let the API generate one via GET /session."
                    ),
                    "workflow": {
                        "1_initialize": (
                            "Call client.get_session_info() to get or create a session_id. "
                            "Store this ID and pass it to CHLAPIClient constructor for auto-injection."
                        ),
                        "2_automatic_tracking": (
                            "POST /entries/read automatically tracks viewed IDs when X-CHL-Session header is present. "
                            "CHLAPIClient auto-injects this header if session_id was provided in __init__."
                        ),
                        "3_manual_marking": (
                            "Optionally call client.mark_entries_cited(entity_ids=[...]) to explicitly mark entries "
                            "the LLM has cited/used in its response (beyond what was read)."
                        ),
                        "4_filtering": (
                            "Use hide_viewed=True in search requests to remove previously seen entries. "
                            "Use downrank_viewed=True to penalize viewed entries (score * 0.5) instead of hiding them. "
                            "Both require X-CHL-Session header."
                        )
                    },
                    "example": {
                        "description": "Typical session usage",
                        "code": (
                            "# Initialize client with session\n"
                            "info = client.get_session_info()  # Get or generate session_id\n"
                            "session_id = info['session_id']\n"
                            "client = CHLAPIClient(session_id=session_id)  # Auto-inject header\n\n"
                            "# Search and read (auto-tracked)\n"
                            "results = client.read_entries(entity_type='experience', category_code='PGS', query='handoff')\n\n"
                            "# Subsequent search hides viewed entries\n"
                            "# (add hide_viewed=True to search request payload)\n\n"
                            "# Clear session if needed\n"
                            "client.clear_session()"
                        )
                    },
                    "limits": {
                        "max_sessions": 500,
                        "ttl_seconds": 3600,
                        "eviction_policy": "LRU (least recently used)"
                    }
                }
            },
        }
    except MCPTransportError as exc:
        logger.error("Failed to build handshake payload: %s", exc)
        return {
            "version": SERVER_VERSION,
            "error": str(exc),
            "tool_index": TOOL_INDEX,
        }
    except Exception as exc:  # pragma: no cover - defensive catch
        logger.error("Failed to build handshake payload: %s", exc)
        return {
            "version": SERVER_VERSION,
            "error": str(exc),
            "tool_index": TOOL_INDEX,
        }


def startup_health_check(client: CHLAPIClient, max_wait: int = 30) -> bool:
    """Check API health on startup.

    Wait up to max_wait seconds for /health to report
    status 'healthy' or 'degraded'.
    """
    # Use monotonic time to avoid issues with system clock adjustments
    start_time = time.monotonic()
    while time.monotonic() - start_time < max_wait:
        try:
            health = client.get_health()
            status = health.get("status")

            if status == "healthy":
                logger.info("API server is healthy")
                return True
            if status == "degraded":
                logger.warning(
                    "API server is degraded but functional: %s",
                    health.get("components"),
                )
                return True

            logger.warning("API server is unhealthy (status=%s), retrying...", status)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Health check failed: %s, retrying...", exc)

        time.sleep(2)

    logger.error("API server did not become healthy within %d seconds", max_wait)
    return False


__all__ = [
    "SERVER_VERSION",
    "TOOL_INDEX",
    "WORKFLOW_MODE_PAYLOAD",
    "HTTP_MODE",
    "CATEGORIES_CACHE_TTL",
    "set_runtime",
    "get_cached_categories",
    "set_categories_cache",
    "invalidate_categories_cache",
    "request_api",
    "build_handshake_payload",
    "startup_health_check",
]
