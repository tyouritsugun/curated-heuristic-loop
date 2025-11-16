"""Shared exceptions for CHL API HTTP client."""

class CHLAPIError(Exception):
    """Base exception for CHL API client errors."""


class APIConnectionError(CHLAPIError):
    """Raised when the API server is not reachable."""


class APIOperationError(CHLAPIError):
    """Raised when an API operation fails."""


