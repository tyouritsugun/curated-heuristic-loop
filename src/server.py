#!/usr/bin/env python3
"""CHL MCP Server - HTTP API Client Shim (Phase 2)

This server acts as a thin HTTP client that forwards MCP tool calls to the
HTTP API server. It provides backward compatibility with existing MCP client
integrations while enabling centralized API-based architecture.

Features:
- Circuit breaker pattern to prevent cascading failures
- Automatic retry with exponential backoff
- Health gating on startup
- Fallback to direct database mode via CHL_MCP_HTTP_MODE=direct (legacy path)

For direct database mode (Phase 1), set CHL_MCP_HTTP_MODE=direct to use mcp_server_direct.py
"""
import argparse
import json
import os
import sys
import logging
import time
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
    {
        "name": "run_import",
        "description": "Trigger import operation via API; returns job id + status.",
        "example": {"payload": {"mode": "full"}}
    },
    {
        "name": "run_export",
        "description": "Trigger export operation via API; returns job id + status.",
        "example": {"payload": {"dest": "sheets"}}
    },
    {
        "name": "rebuild_index",
        "description": "Trigger index maintenance via API operations; returns job id + status.",
        "example": {}
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


def _parse_http_mode_override() -> Optional[str]:
    """Parse optional CLI flag for MCP HTTP mode."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--chl-http-mode",
        choices=["http", "auto", "direct"],
        dest="chl_http_mode",
        help="Override CHL_MCP_HTTP_MODE for this process",
    )
    args, _ = parser.parse_known_args()
    return args.chl_http_mode


CLI_HTTP_MODE = _parse_http_mode_override()
HTTP_MODE = "http"
CATEGORIES_CACHE_TTL = 30.0
_categories_cache: Dict[str, Any] = {"payload": None, "expires": 0.0}
_direct_handlers: Dict[str, Callable] = {}

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


def _http_enabled() -> bool:
    return HTTP_MODE in ("http", "auto")


def _auto_mode_enabled() -> bool:
    return HTTP_MODE == "auto"


def _get_cached_categories() -> Optional[Dict[str, Any]]:
    if not _http_enabled():
        return None
    payload = _categories_cache.get("payload")
    expires = _categories_cache.get("expires", 0.0)
    if payload is None or expires < time.time():
        return None
    return payload


def _set_categories_cache(payload: Dict[str, Any], source: str) -> None:
    if source != "http":
        return
    _categories_cache["payload"] = payload
    _categories_cache["expires"] = time.time() + CATEGORIES_CACHE_TTL


def _ensure_direct_handlers() -> Dict[str, Callable]:
    """Load direct MCP handlers for fallback or auto mode."""
    global _direct_handlers

    if _direct_handlers:
        return _direct_handlers

    from src import mcp_server_direct as direct_server  # Lazy import to avoid recursion
    handler_names = [
        "list_categories",
        "read_entries",
        "write_entry",
        "update_entry",
        "delete_entry",
        "get_guidelines",
    ]
    _direct_handlers = {name: getattr(direct_server, name) for name in handler_names}
    logger.info("Direct MCP handlers loaded for fallback mode.")
    return _direct_handlers


def _call_direct_handler(name: str, **kwargs) -> Dict[str, Any]:
    handler = _ensure_direct_handlers().get(name)
    if handler is None:
        raise MCPError(f"Direct handler '{name}' is unavailable")
    return handler(**kwargs)


def _request_with_fallback(
    method: str,
    path: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    fallback_name: Optional[str] = None,
    fallback_kwargs: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, Any], str]:
    """Call API and optionally fall back to the direct handler."""
    fallback_kwargs = fallback_kwargs or {}

    if _http_enabled() and api_client is not None:
        try:
            request_kwargs: Dict[str, Any] = {"json": payload} if payload is not None else {}
            if headers:
                # Merge/override default headers
                request_kwargs["headers"] = headers
            return api_client.request(method, path, **request_kwargs), "http"
        except MCPTransportError as exc:
            if _auto_mode_enabled() and fallback_name:
                logger.warning(
                    "HTTP %s %s failed (%s); falling back to direct handler '%s'",
                    method,
                    path,
                    exc,
                    fallback_name,
                )
            else:
                raise

    if fallback_name:
        return _call_direct_handler(fallback_name, **fallback_kwargs), "direct"

    raise MCPError("HTTP client unavailable and no fallback handler configured.")


def _build_direct_handshake_payload(reason: Optional[str] = None) -> Dict[str, Any]:
    try:
        categories_response = _call_direct_handler("list_categories")
        categories = categories_response.get("categories", [])
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Failed to build direct handshake payload: %s", exc)
        categories = []
        reason = reason or str(exc)

    search_payload = {
        "primary_provider": "sqlite_text",
        "vector_available": False,
        "fallback_enabled": False,
        "status": "direct",
    }
    if reason:
        search_payload["hint"] = reason

    return {
        "version": SERVER_VERSION,
        "workflow_mode": _workflow_mode_payload(),
        "tool_index": TOOL_INDEX,
        "search": search_payload,
        "categories": categories,
        "mode": {
            "transport": "direct",
            "requested": HTTP_MODE,
        },
    }


def _build_handshake_payload() -> Dict[str, Any]:
    """Build startup instructions payload from API server."""
    if not _http_enabled() or api_client is None:
        return _build_direct_handshake_payload()

    try:
        categories_data = api_client.request("GET", "/api/v1/categories/")
        _set_categories_cache(categories_data, "http")

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
        if _auto_mode_enabled():
            logger.warning("API unavailable during handshake (%s); using direct fallback.", exc)
            return _build_direct_handshake_payload(reason=str(exc))
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
    global config, api_client, _initialized, HTTP_MODE

    if _initialized:
        logger.info("init_server() called after initialization; reusing existing services.")
        try:
            mcp.instructions = json.dumps(_build_handshake_payload())
        except Exception as e:  # pragma: no cover - defensive
            mcp.instructions = json.dumps(create_error_response("SERVER_ERROR", str(e), retryable=False))
        return

    config = get_config()

    # Apply CLI override if provided
    if CLI_HTTP_MODE:
        logger.info("CLI override detected: --chl-http-mode=%s", CLI_HTTP_MODE)
        config.mcp_http_mode = CLI_HTTP_MODE
        config.use_api = CLI_HTTP_MODE != "direct"

    HTTP_MODE = config.mcp_http_mode

    try:
        _setup_logging(config)
    except Exception as e:  # pragma: no cover - logging setup best-effort
        print(f"Warning: failed to initialize file logging: {e}")

    logger.info(
        "MCP HTTP mode resolved to '%s' (base_url=%s)",
        HTTP_MODE,
        config.api_base_url if config.use_api else "n/a",
    )

    if HTTP_MODE == "direct":
        logger.warning("MCP HTTP mode set to 'direct'; delegating to legacy MCP server.")
        from src.mcp_server_direct import mcp as direct_mcp, init_server as direct_init_server

        direct_init_server()
        globals()['mcp'] = direct_mcp
        _initialized = True
        return

    logger.info("Initializing HTTP API client: %s", config.api_base_url)
    api_client = APIClient(
        base_url=config.api_base_url,
        timeout=config.api_timeout,
        circuit_breaker_threshold=config.api_circuit_breaker_threshold,
        circuit_breaker_timeout=config.api_circuit_breaker_timeout,
    )

    health_ok = startup_health_check(api_client, max_wait=config.api_health_check_max_wait)
    if not health_ok:
        if HTTP_MODE == "http":
            logger.error(
                "Cannot start MCP server: API is unavailable. "
                "Ensure API server is running or set CHL_MCP_HTTP_MODE=direct for legacy mode."
            )
            sys.exit(1)
        logger.warning("API health check failed; auto mode will fall back to direct handlers as needed.")

    mcp.tool()(list_categories)
    mcp.tool()(read_entries)
    mcp.tool()(write_entry)
    mcp.tool()(update_entry)
    mcp.tool()(delete_entry)
    mcp.tool()(get_guidelines)
    mcp.tool()(run_import)
    mcp.tool()(run_export)
    mcp.tool()(rebuild_index)

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
        payload, source = _request_with_fallback(
            "GET",
            "/api/v1/categories/",
            fallback_name="list_categories" if _auto_mode_enabled() else None,
        )
        _set_categories_cache(payload, source)
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

        response, _ = _request_with_fallback(
            "POST",
            "/api/v1/entries/read",
            payload=payload,
            fallback_name="read_entries" if _auto_mode_enabled() else None,
            fallback_kwargs={
                "entity_type": entity_type,
                "category_code": category_code,
                "query": query,
                "ids": ids,
                "limit": limit,
            },
        )
        return response
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
        response, _ = _request_with_fallback(
            "POST",
            "/api/v1/entries/write",
            payload=payload,
            fallback_name="write_entry" if _auto_mode_enabled() else None,
            fallback_kwargs={
                "entity_type": entity_type,
                "category_code": category_code,
                "data": data,
            },
        )
        return response
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
        response, _ = _request_with_fallback(
            "POST",
            "/api/v1/entries/update",
            payload=payload,
            fallback_name="update_entry" if _auto_mode_enabled() else None,
            fallback_kwargs={
                "entity_type": entity_type,
                "category_code": category_code,
                "entry_id": entry_id,
                "updates": updates,
                "force_contextual": force_contextual,
            },
        )
        return response
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
        response, _ = _request_with_fallback(
            "DELETE",
            "/api/v1/entries/delete",
            payload=payload,
            fallback_name="delete_entry" if _auto_mode_enabled() else None,
            fallback_kwargs={
                "entity_type": entity_type,
                "category_code": category_code,
                "entry_id": entry_id,
            },
        )
        return response
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

        response, _ = _request_with_fallback(
            "GET",
            path,
            fallback_name="get_guidelines" if _auto_mode_enabled() else None,
            fallback_kwargs={"guide_type": guide_type, "version": version},
        )
        return response
    except MCPError:
        raise
    except Exception as e:  # pragma: no cover - defensive
        logger.exception(f"Unexpected error in get_guidelines: {e}")
        raise MCPError(f"Unexpected error: {e}")


def _actor_header() -> Dict[str, str]:
    """Best-effort actor header for operations audit trail."""
    try:
        import getpass
        actor = getpass.getuser() or "unknown"
    except Exception:
        actor = "unknown"
    return {"x-actor": actor}


def run_import(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Trigger an import job via API operations endpoint."""
    try:
        body = {"payload": payload} if payload else {}
        response, _ = _request_with_fallback(
            "POST",
            "/api/v1/operations/import",
            payload=body,
            headers=_actor_header(),
        )
        return response
    except MCPError:
        raise
    except Exception as e:  # pragma: no cover - defensive
        logger.exception(f"Unexpected error in run_import: {e}")
        raise MCPError(f"Unexpected error: {e}")


def run_export(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Trigger an export job via API operations endpoint."""
    try:
        body = {"payload": payload} if payload else {}
        response, _ = _request_with_fallback(
            "POST",
            "/api/v1/operations/export",
            payload=body,
            headers=_actor_header(),
        )
        return response
    except MCPError:
        raise
    except Exception as e:  # pragma: no cover - defensive
        logger.exception(f"Unexpected error in run_export: {e}")
        raise MCPError(f"Unexpected error: {e}")


def rebuild_index(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Trigger an index maintenance job via API operations endpoint."""
    try:
        body = {"payload": payload} if payload else {}
        response, _ = _request_with_fallback(
            "POST",
            "/api/v1/operations/index",
            payload=body,
            headers=_actor_header(),
        )
        return response
    except MCPError:
        raise
    except Exception as e:  # pragma: no cover - defensive
        logger.exception(f"Unexpected error in rebuild_index: {e}")
        raise MCPError(f"Unexpected error: {e}")


# Initialize on module load unless explicitly skipped (useful for tests)
if os.getenv("CHL_SKIP_MCP_AUTOSTART", "0") != "1":
    init_server()


if __name__ == "__main__":
    # Run MCP server when executed directly
    mcp.run()
