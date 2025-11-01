"""MCP server utility helpers."""
from typing import Dict, Any
import json


def create_error_response(code: str, message: str) -> Dict[str, Any]:
    """Create standardized error response payload for MCP tools."""
    return {
        "error": {
            "code": code,
            "message": message,
        }
    }


def normalize_context(raw_context: Any) -> Any:
    """Return context data as structured JSON when stored as a serialized string."""
    if raw_context is None:
        return None
    if isinstance(raw_context, (dict, list)):
        return raw_context

    if isinstance(raw_context, str):
        stripped = raw_context.strip()
        if not stripped:
            return None
        try:
            decoded = json.loads(stripped)
            if isinstance(decoded, (dict, list)):
                return decoded
            return decoded
        except json.JSONDecodeError:
            return raw_context

    return raw_context
