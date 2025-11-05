"""Custom exceptions and exception handlers for the API."""

from fastapi import Request
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger(__name__)


# Custom exceptions
class EntityNotFoundError(Exception):
    """Raised when an entity is not found."""
    pass


class DuplicateEntityError(Exception):
    """Raised when a duplicate entity is detected."""
    pass


class ValidationError(Exception):
    """Raised on validation failures."""
    pass


# Exception handlers
async def entity_not_found_handler(request: Request, exc: EntityNotFoundError):
    """Handle EntityNotFoundError."""
    return JSONResponse(
        status_code=404,
        content={"error": "Entity not found", "detail": str(exc)}
    )


async def duplicate_entity_handler(request: Request, exc: DuplicateEntityError):
    """Handle DuplicateEntityError."""
    return JSONResponse(
        status_code=409,
        content={"error": "Duplicate entity", "detail": str(exc)}
    )


async def validation_error_handler(request: Request, exc: ValidationError):
    """Handle ValidationError."""
    return JSONResponse(
        status_code=400,
        content={"error": "Validation failed", "detail": str(exc)}
    )


async def generic_exception_handler(request: Request, exc: Exception):
    """Handle all unhandled exceptions."""
    logger.exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": "An unexpected error occurred"
        }
    )
