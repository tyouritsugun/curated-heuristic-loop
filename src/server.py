#!/usr/bin/env python3
"""CHL MCP Server - HTTP API Client

This server acts as a thin HTTP client that forwards MCP tool calls to the
HTTP API server. It provides backward compatibility with existing MCP client
integrations while enabling centralized API-based architecture.

Features:
- Circuit breaker pattern to prevent cascading failures
- Automatic retry with exponential backoff
- Health gating on startup
"""
import argparse
import json
import os
import sys
import logging
import time
import threading
from copy import deepcopy
from pathlib import Path
from typing import Dict, Any, Optional, Callable, Tuple

# Add parent directory to path for absolute imports
src_dir = Path(__file__).parent
if str(src_dir.parent) not in sys.path:
    sys.path.insert(0, str(src_dir.parent))

from fastmcp import FastMCP
from src.config import get_config
from src.mcp.api_client import APIClient, startup_health_check
from src.mcp.errors import MCPError, MCPTransportError
from src.mcp.utils import create_error_response

logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP("CHL MCP Server")

SERVER_VERSION = "1.1.0"

TOOL_INDEX = [
    {
        "name": "list_categories",
        "description": "List all available category shelves with code and name.",
        "example": {}
    },
    {
        "name": "read_entries",
        "description": "Fetch experiences or manuals by ids or semantic query.",
        "example": {
            "entity_type": "experience",
            "category_code": "PGS",
            "query": "handoff checklist"
        },
    },
    {
        "name": "write_entry",
        "description": "Create a new experience or manual in a category.",
        "example": {
            "entity_type": "experience",
            "category_code": "PGS",
            "data": {
                "section": "useful",
                "title": "Review breakpoints before spec",
                "playbook": "Confirm responsive states with design before writing HTML."
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
            "updates": {"summary": "Adds audit checklist step."}
        },
    },
    {
        "name": "delete_entry",
        "description": "Delete a manual entry (experiences cannot be deleted via MCP).",
        "example": {
            "entity_type": "manual",
            "category_code": "PGS",
            "entry_id": "MNL-PGS-20250115-104200123456"
        },
    },
    {
        "name": "get_guidelines",
        "description": "Return the generator or evaluator workflow manual seeded in GLN.",
        "example": {"guide_type": "generator"}
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
        "evaluator": "Use guide_type='evaluator' only after generator work is done."
    },
}


# HTTP mode is always enabled - no CLI override needed
HTTP_MODE = "http"
# Allow TTL to be configured for faster responsiveness after settings changes
try:
    CATEGORIES_CACHE_TTL = float(os.getenv("CHL_CATEGORIES_CACHE_TTL", "30.0"))
except (TypeError, ValueError):
    CATEGORIES_CACHE_TTL = 30.0
_categories_cache: Dict[str, Any] = {"payload": None, "expires": 0.0}
_categories_cache_lock = threading.Lock()

# Global state
config = None
api_client: Optional[APIClient] = None
_initialized = False


def _setup_logging(config) -> None:
    """Configure root logger with console and rotating file handler."""
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    level = getattr(logging, str(getattr(config, 'log_level', 'INFO')).upper(), logging.INFO)
    root.setLevel(level)

    # Create formatters
    fmt = logging.Formatter(
        fmt="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Avoid duplicate handlers if reloaded
    existing_targets = set()
    for h in list(root.handlers):
        target = getattr(h, 'baseFilename', None) or getattr(h, 'stream', None)
        existing_targets.add(target)

    # Console handler
    if sys.stdout not in existing_targets:
        ch = logging.StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    # File handler
    try:
        log_dir = Path(getattr(config, 'experience_root', 'data')) / 'log'
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / 'chl_server.log'

        # Convert to string for comparison with existing targets
        if str(log_path) not in existing_targets:
            fh = RotatingFileHandler(str(log_path), maxBytes=5_242_880, backupCount=3)
            fh.setLevel(level)
            fh.setFormatter(fmt)
            root.addHandler(fh)

        logging.getLogger(__name__).info(f"Logging initialized. Level={logging.getLevelName(level)}, file={log_path}")
    except Exception as e:
        logger.warning(f"Failed to initialize file logging: {e}")


def _workflow_mode_payload() -> Dict[str, Any]:
    """Return a copy of the workflow mode instructions."""
    return deepcopy(WORKFLOW_MODE_PAYLOAD)


def _get_cached_categories() -> Optional[Dict[str, Any]]:
    with _categories_cache_lock:
        payload = _categories_cache.get("payload")
        expires = _categories_cache.get("expires", 0.0)
        if payload is None or expires < time.time():
            return None
        return payload


def _set_categories_cache(payload: Dict[str, Any]) -> None:
    with _categories_cache_lock:
        _categories_cache["payload"] = payload
        _categories_cache["expires"] = time.time() + CATEGORIES_CACHE_TTL


def invalidate_categories_cache() -> None:
    """Clear categories cache, e.g., after settings changes."""
    with _categories_cache_lock:
        _categories_cache["payload"] = None
        _categories_cache["expires"] = 0.0


def _request_api(
    method: str,
    path: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Call API server."""
    if api_client is None:
        raise MCPError("HTTP client not initialized")

    request_kwargs: Dict[str, Any] = {"json": payload} if payload is not None else {}
    if headers:
        request_kwargs["headers"] = headers
    return api_client.request(method, path, **request_kwargs)


def _build_handshake_payload() -> Dict[str, Any]:
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
        _set_categories_cache(categories_data)

        health_data = api_client.check_health()
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
            "workflow_mode": _workflow_mode_payload(),
            "tool_index": TOOL_INDEX,
            "search": search_payload,
            "categories": categories_data.get("categories", []),
            "mode": {
                "transport": HTTP_MODE,
                "base_url": getattr(config, "api_base_url", "unknown"),
            },
        }
    except MCPTransportError as exc:
        logger.error("Failed to build handshake payload: %s", exc)
        return {
            "version": SERVER_VERSION,
            "error": str(exc),
            "tool_index": TOOL_INDEX,
        }
    except Exception as e:  # pragma: no cover - defensive catch
        logger.error("Failed to build handshake payload: %s", e)
        return {
            "version": SERVER_VERSION,
            "error": str(e),
            "tool_index": TOOL_INDEX,
        }


def init_server():
    """Initialize server with configuration and API client."""
    global config, api_client, _initialized

    if _initialized:
        logger.info("init_server() called after initialization; reusing existing services.")
        try:
            mcp.instructions = json.dumps(_build_handshake_payload())
        except Exception as e:  # pragma: no cover - defensive
            mcp.instructions = json.dumps(create_error_response("SERVER_ERROR", str(e), retryable=False))
        return

    config = get_config()

    try:
        _setup_logging(config)
    except Exception as e:  # pragma: no cover - logging setup best-effort
        print(f"Warning: failed to initialize file logging: {e}")

    logger.info("Initializing HTTP API client: %s", config.api_base_url)
    api_client = APIClient(
        base_url=config.api_base_url,
        timeout=config.api_timeout,
        circuit_breaker_threshold=config.api_circuit_breaker_threshold,
        circuit_breaker_timeout=config.api_circuit_breaker_timeout,
    )

    health_ok = startup_health_check(api_client, max_wait=config.api_health_check_max_wait)
    if not health_ok:
        logger.error(
            "Cannot start MCP server: API is unavailable. "
            "Ensure API server is running at %s",
            config.api_base_url
        )
        sys.exit(1)

    mcp.tool()(list_categories)
    mcp.tool()(read_entries)
    mcp.tool()(write_entry)
    mcp.tool()(update_entry)
    mcp.tool()(delete_entry)
    mcp.tool()(get_guidelines)

    try:
        mcp.instructions = json.dumps(_build_handshake_payload())
    except Exception as e:  # pragma: no cover - defensive
        mcp.instructions = json.dumps(create_error_response("SERVER_ERROR", str(e), retryable=False))
    else:
        _initialized = True


# Tool implementations - HTTP API shim

def list_categories() -> Dict[str, Any]:
    """
    Return the current set of available category shelves.

    Example:
        {}

    Returns:
        Dictionary with 'categories' list containing code and name for each category
    """
    cached = _get_cached_categories()
    if cached is not None:
        return cached

    try:
        payload = _request_api("GET", "/api/v1/categories/")
        _set_categories_cache(payload)
        return payload
    except MCPError:
        raise
    except Exception as e:  # pragma: no cover - defensive
        logger.exception(f"Unexpected error in list_categories: {e}")
        raise MCPError(f"Unexpected error: {e}")


def read_entries(
    entity_type: str,
    category_code: str,
    query: str = None,
    ids: list = None,
    limit: int = None
) -> Dict[str, Any]:
    """
    Retrieve experiences or manuals from a category by ids or semantic query.

    Example:
        read_entries(entity_type='experience', category_code='PGS', query='handoff checklist')
    """
    try:
        payload = {
            "entity_type": entity_type,
            "category_code": category_code,
        }
        if query is not None:
            payload["query"] = query
        if ids is not None:
            payload["ids"] = ids
        if limit is not None:
            payload["limit"] = limit

        return _request_api("POST", "/api/v1/entries/read", payload=payload)
    except MCPError:
        raise
    except Exception as e:  # pragma: no cover - defensive
        logger.exception(f"Unexpected error in read_entries: {e}")
        raise MCPError(f"Unexpected error: {e}")


def write_entry(
    entity_type: str,
    category_code: str,
    data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Create a new experience or manual entry in the requested category.

    Args:
        entity_type: Either 'experience' or 'manual'
        category_code: Category shelf code (e.g., 'PGS', 'GLN')
        data: Entry payload

    Payload schema:
        For entity_type='experience':
            Required fields:
                - section (str): One of 'useful', 'harmful', or 'contextual'
                - title (str): 1-120 characters
                - playbook (str): 1-2000 characters
            Optional fields:
                - context (object|string|null): Ignored unless section='contextual'

        For entity_type='manual':
            Required fields:
                - title (str): 1-120 characters
                - content (str): non-empty
            Optional fields:
                - summary (str|null)

    Returns:
        JSON with:
            - success (bool)
            - entry_id (str)
            - entry (object): Full entry for read-after-write (includes embedding_status)
            - duplicates (list): Potential duplicates with scores and summaries
            - recommendation (str): Suggested action based on similarity
            - message (str): Notes on async indexing readiness

    Example:
        write_entry('experience', 'PGS', {
            'section': 'useful',
            'title': 'Checklist the spec handoff',
            'playbook': 'Review the Figma comments before handoff.',
            'context': {'note': 'Only used when section is contextual.'}
        })
    """
    try:
        payload = {
            "entity_type": entity_type,
            "category_code": category_code,
            "data": data,
        }
        return _request_api("POST", "/api/v1/entries/write", payload=payload)
    except MCPError:
        raise
    except Exception as e:  # pragma: no cover - defensive
        logger.exception(f"Unexpected error in write_entry: {e}")
        raise MCPError(f"Unexpected error: {e}")


def update_entry(
    entity_type: str,
    category_code: str,
    entry_id: str,
    updates: Dict[str, Any],
    force_contextual: bool = False
) -> Dict[str, Any]:
    """
    Update an existing experience or manual entry by id.

    Allowed fields:
        - experience: title, playbook, context, section (use force_contextual=true to set section='contextual')
        - manual: title, content, summary

    Returns:
        JSON with success, entry_id, entry (full object), and message.

    Example:
        update_entry('manual', 'PGS', 'MNL-PGS-20250115-104200123456', {
            'summary': 'Adds audit checklist step.'
        })
    """
    try:
        payload = {
            "entity_type": entity_type,
            "category_code": category_code,
            "entry_id": entry_id,
            "updates": updates,
            "force_contextual": force_contextual,
        }
        return _request_api("POST", "/api/v1/entries/update", payload=payload)
    except MCPError:
        raise
    except Exception as e:  # pragma: no cover - defensive
        logger.exception(f"Unexpected error in update_entry: {e}")
        raise MCPError(f"Unexpected error: {e}")


def delete_entry(
    entity_type: str,
    category_code: str,
    entry_id: str
) -> Dict[str, Any]:
    """
    Delete a manual entry from a category (experiences cannot be deleted).

    Example:
        delete_entry('manual', 'PGS', 'MNL-PGS-20250115-104200123456')
    """
    try:
        payload = {
            "entity_type": entity_type,
            "category_code": category_code,
            "entry_id": entry_id,
        }
        return _request_api("DELETE", "/api/v1/entries/delete", payload=payload)
    except MCPError:
        raise
    except Exception as e:  # pragma: no cover - defensive
        logger.exception(f"Unexpected error in delete_entry: {e}")
        raise MCPError(f"Unexpected error: {e}")


def get_guidelines(guide_type: str, version: str = None) -> Dict[str, Any]:
    """
    Return the generator or evaluator workflow manual from the GLN category.

    Example:
        get_guidelines(guide_type='generator')
    """
    try:
        params = {}
        if version is not None:
            params["version"] = version

        path = f"/api/v1/guidelines/{guide_type}"
        if params:
            from urllib.parse import urlencode

            path = f"{path}?{urlencode(params)}"

        return _request_api("GET", path)
    except MCPError:
        raise
    except Exception as e:  # pragma: no cover - defensive
        logger.exception(f"Unexpected error in get_guidelines: {e}")
        raise MCPError(f"Unexpected error: {e}")


# Operations management tools (run_import, run_export, rebuild_index, job_status,
# cancel_job, list_jobs) have been removed from MCP interface.
# These operations should be managed through the web UI at /operations.


# Initialize on module load unless explicitly skipped (useful for tests)
if os.getenv("CHL_SKIP_MCP_AUTOSTART", "0") != "1":
    init_server()


if __name__ == "__main__":
    # Run MCP server when executed directly
    mcp.run()
