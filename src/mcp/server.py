"""CHL MCP Server - HTTP API client entrypoint."""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from src.common.api_client.client import CHLAPIClient
from src.common.config.config import get_config
from src.mcp.core import (
    SERVER_VERSION,
    TOOL_INDEX,
    set_runtime,
    build_handshake_payload,
    startup_health_check,
)
from src.mcp.errors import MCPError
from src.mcp.utils import create_error_response
from src.mcp.handlers_entries import (
    list_categories,
    read_entries,
    create_entry,
    update_entry,
    check_duplicates,
)
from src.mcp.handlers_guidelines import get_guidelines

logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP("CHL: Skill & experience toolset - clarify task intent before action")

# Global state
config: Any = None
api_client: CHLAPIClient | None = None
_initialized = False


def _setup_logging(config_obj) -> None:
    """Configure root logger with console and rotating file handler."""
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    level = getattr(
        logging,
        str(getattr(config_obj, "log_level", "INFO")).upper(),
        logging.INFO,
    )
    root.setLevel(level)

    fmt = logging.Formatter(
        fmt="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Avoid duplicate handlers if reloaded
    existing_targets = set()
    for handler in list(root.handlers):
        target = getattr(handler, "baseFilename", None) or getattr(handler, "stream", None)
        existing_targets.add(target)

    # Console handler
    if sys.stdout not in existing_targets:
        ch = logging.StreamHandler()
        ch.setLevel(level)
        ch.setFormatter(fmt)
        root.addHandler(ch)

    # File handler
    try:
        log_dir = Path(getattr(config_obj, "experience_root", "data")) / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "chl_server.log"

        if str(log_path) not in existing_targets:
            fh = RotatingFileHandler(str(log_path), maxBytes=5_242_880, backupCount=3)
            fh.setLevel(level)
            fh.setFormatter(fmt)
            root.addHandler(fh)

        logging.getLogger(__name__).info(
            "Logging initialized. Level=%s, file=%s",
            logging.getLevelName(level),
            log_path,
        )
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Failed to initialize file logging: %s", exc)


def init_server() -> None:
    """Initialize MCP server with configuration and API client."""
    global config, api_client, _initialized

    if _initialized:
        logger.info(
            "init_server() called after initialization; reusing existing services."
        )
        try:
            mcp.instructions = json.dumps(build_handshake_payload())
        except Exception as exc:  # pragma: no cover - defensive
            mcp.instructions = json.dumps(
                create_error_response("SERVER_ERROR", str(exc), retryable=False)
            )
        return

    config = get_config()

    try:
        _setup_logging(config)
    except Exception as exc:  # pragma: no cover - logging setup best-effort
        print(f"Warning: failed to initialize file logging: {exc}")

    logger.info("Initializing HTTP API client: %s", config.api_base_url)

    # Session ID management - check env override first, then auto-generate
    # This enables automatic session memory without user action
    session_id = os.getenv("CHL_SESSION_ID")

    if session_id:
        logger.info("Using session ID from CHL_SESSION_ID env: %s", session_id)
    else:
        # Auto-generate session ID via API
        temp_client = CHLAPIClient(base_url=config.api_base_url, timeout=config.api_timeout)
        try:
            session_info = temp_client.get_session_info()
            session_id = session_info['session_id']
            logger.info("Auto-generated session ID: %s", session_id)
        except Exception as exc:
            logger.warning("Failed to auto-generate session ID, continuing without session: %s", exc)
            session_id = None
        finally:
            temp_client.session.close()

    # Create main client with session ID injected
    api_client = CHLAPIClient(
        base_url=config.api_base_url,
        timeout=config.api_timeout,
        session_id=session_id,
    )

    # Expose runtime to core module for handlers
    set_runtime(config, api_client)
    health_ok = startup_health_check(api_client, max_wait=config.api_health_check_max_wait)
    if not health_ok:
        logger.error(
            "Cannot start MCP server: API is unavailable. Ensure API server is running at %s",
            config.api_base_url,
        )
        sys.exit(1)

    # Log session status for diagnostics
    if session_id:
        logger.info("Session memory ENABLED (session_id=%s). Viewed entries will be tracked.", session_id)
    else:
        logger.warning(
            "Session memory DISABLED. Session-aware features (hide_viewed, downrank_viewed) will not work. "
            "Set CHL_SESSION_ID env var or ensure /api/v1/session endpoint is available."
        )

    # Register tools
    mcp.tool()(list_categories)
    mcp.tool()(read_entries)
    mcp.tool()(create_entry)
    mcp.tool()(update_entry)
    mcp.tool()(check_duplicates)
    mcp.tool()(get_guidelines)

    # ------------------------------------------------------------------
    # MCP Resources (official spec discovery endpoints)
    # ------------------------------------------------------------------

    @mcp.resource("chl://guidelines/generator")
    def resource_generator_guidelines() -> str:
        """Full generator workflow guidance markdown."""
        path = Path(__file__).resolve().parents[2] / "generator.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return "# generator.md not found\n"

    @mcp.resource("chl://guidelines/evaluator")
    def resource_evaluator_guidelines() -> str:
        """Full evaluator workflow guidance markdown."""
        path = Path(__file__).resolve().parents[2] / "evaluator.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return "# evaluator.md not found\n"

    @mcp.resource("chl://guidelines/evaluator_cpu")
    def resource_evaluator_cpu_guidelines() -> str:
        """Evaluator (CPU) guidance markdown with duplicate-check fallback notes."""
        path = Path(__file__).resolve().parents[2] / "evaluator_cpu.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return "# evaluator_cpu.md not found\n"

    @mcp.resource("chl://categories/index")
    def resource_categories_index() -> str:
        """Cached category index (same payload as list_categories)."""
        try:
            return json.dumps(list_categories(), ensure_ascii=False, indent=2)
        except Exception as exc:  # pragma: no cover - defensive
            return json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2)

    @mcp.resource("chl://runtime/config")
    def resource_runtime_config() -> str:
        """Current runtime configuration detected by env check."""
        path = Path(__file__).resolve().parents[2] / "data" / "runtime_config.json"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return json.dumps({"warning": "runtime_config.json not found"}, indent=2)

    @mcp.resource("chl://entry/{entry_id}")
    def resource_entry_by_id(entry_id: str) -> str:
        """Fetch a single entry by ID (EXP-* for experiences, MNL-* for skills), returns JSON."""
        upper = entry_id.upper()
        if upper.startswith("EXP-"):
            entity_type = "experience"
            fields = ["playbook", "section", "title"]
        elif upper.startswith("MNL-"):
            entity_type = "skill"
            fields = ["content", "summary", "title"]
        else:
            raise MCPError("entry_id must start with EXP- or MNL-")

        resp = read_entries(entity_type=entity_type, ids=[entry_id], fields=fields)
        entries = resp.get("entries") or []
        if not entries:
            raise MCPError(f"Entry not found: {entry_id}")
        return json.dumps(entries[0], ensure_ascii=False, indent=2)

    @mcp.resource("chl://category/{category_code}/latest{?entity_type,limit}")
    def resource_category_latest(category_code: str, entity_type: str = "experience", limit: int = 10) -> str:
        """Latest entries in a category (previews unless fields requested). entity_type: 'experience' or 'skill'."""
        entity_type = entity_type or "experience"
        if entity_type not in {"experience", "skill"}:
            raise MCPError("entity_type must be 'experience' or 'skill'")
        limit_int = int(limit)
        resp = read_entries(entity_type=entity_type, category_code=category_code, limit=limit_int)
        return json.dumps(resp, ensure_ascii=False, indent=2)

    try:
        mcp.instructions = json.dumps(build_handshake_payload())
    except Exception as exc:  # pragma: no cover - defensive
        mcp.instructions = json.dumps(
            create_error_response("SERVER_ERROR", str(exc), retryable=False)
        )
    else:
        _initialized = True


# Initialize on module load unless explicitly skipped (useful for tests)
if os.getenv("CHL_SKIP_MCP_AUTOSTART", "0") != "1":
    init_server()


if __name__ == "__main__":
    # Run MCP server when executed directly
    mcp.run()
