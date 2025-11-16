"""Bridge helpers for optional MCP → API callbacks without hard dependencies."""

from __future__ import annotations

from typing import Callable, Optional
import logging

logger = logging.getLogger(__name__)

_categories_invalidator: Optional[Callable[[], None]] = None


def register_categories_invalidator(callback: Callable[[], None]) -> None:
    """Register a callback that invalidates MCP categories cache."""
    global _categories_invalidator
    _categories_invalidator = callback
    logger.debug("Registered MCP categories invalidator: %s", callback)


def invalidate_categories_cache() -> None:
    """Invoke the registered categories cache invalidator, if any."""
    if _categories_invalidator is None:
        logger.debug("No MCP categories invalidator registered; skipping.")
        return
    try:
        _categories_invalidator()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Categories invalidator raised %s", exc, exc_info=True)
