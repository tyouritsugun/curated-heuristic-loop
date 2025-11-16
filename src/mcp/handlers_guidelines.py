"""MCP tool handler for guidelines retrieval."""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.mcp.errors import MCPError
from src.mcp.core import request_api


def get_guidelines(guide_type: str, version: Optional[str] = None) -> Dict[str, Any]:
    """
    Return the generator or evaluator workflow manual from the GLN category.

    Example:
        get_guidelines(guide_type='generator')
    """
    try:
        params: Dict[str, Any] = {}
        if version is not None:
            params["version"] = version

        path = f"/api/v1/guidelines/{guide_type}"
        if params:
            from urllib.parse import urlencode

            path = f"{path}?{urlencode(params)}"

        return request_api("GET", path)
    except MCPError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise MCPError(f"Unexpected error: {exc}") from exc


__all__ = ["get_guidelines"]

