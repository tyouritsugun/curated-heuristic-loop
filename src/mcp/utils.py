"""MCP server utility helpers."""
from typing import Dict, Any, Optional
import json


def create_error_response(
    code: str,
    message: str,
    *,
    hint: Optional[str] = None,
    retryable: Optional[bool] = None,
) -> Dict[str, Any]:
    """Create standardized error response payload for MCP tools."""
    error_payload: Dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
        }
    }
    if hint is not None:
        error_payload["error"]["hint"] = hint
    if retryable is not None:
        error_payload["error"]["retryable"] = retryable
    return error_payload


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
