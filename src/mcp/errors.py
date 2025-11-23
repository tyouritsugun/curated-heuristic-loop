"""Error translation between HTTP and MCP."""
import httpx


class MCPError(Exception):
    """Base exception for MCP errors."""
    pass


class MCPValidationError(MCPError):
    """Validation error (maps to 400)."""
    pass


class MCPNotFoundError(MCPError):
    """Entity not found (maps to 404)."""
    pass


class MCPConflictError(MCPError):
    """Conflict/duplicate error (maps to 409)."""
    pass


class MCPServerError(MCPError):
    """Internal server error (maps to 500)."""
    pass


class MCPTransportError(MCPServerError):
    """Network/transport failure talking to the API server."""
    pass


def translate_http_error(http_error: httpx.HTTPStatusError) -> MCPError:
    """
    Translate HTTP error to MCP error.

    Error mapping:
    - 400 Bad Request → MCPValidationError
    - 404 Not Found → MCPNotFoundError
    - 409 Conflict → MCPConflictError
    - 500 Internal Server Error → MCPServerError
    - 503 Service Unavailable → MCPServerError (with retry message)
    - Other → MCPError
    """
    status_code = http_error.response.status_code

    # Try to parse JSON error detail
    try:
        response_body = http_error.response.json() if http_error.response.text else {}
        error_detail = response_body.get("detail", str(http_error))
    except Exception:
        error_detail = str(http_error)

    if status_code == 400:
        return MCPValidationError(f"Validation failed: {error_detail}")
    elif status_code == 404:
        return MCPNotFoundError(f"Not found: {error_detail}")
    elif status_code == 409:
        return MCPConflictError(f"Conflict: {error_detail}")
    elif status_code == 503:
        return MCPServerError(
            f"API server is temporarily unavailable: {error_detail}. "
            "Please try again in a few moments."
        )
    elif status_code >= 500:
        return MCPServerError(f"Server error: {error_detail}")
    else:
        return MCPError(f"API request failed ({status_code}): {error_detail}")
