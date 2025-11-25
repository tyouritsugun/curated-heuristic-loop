"""Snippet generation utilities for search results."""

from typing import Optional, Tuple


def generate_snippet(
    text: Optional[str],
    max_length: int = 320,
    add_ellipsis: bool = True
) -> Tuple[Optional[str], bool]:
    """Generate a snippet from text content.

    Args:
        text: Source text to truncate
        max_length: Maximum snippet length in characters
        add_ellipsis: Whether to add "..." to truncated snippets

    Returns:
        Tuple of (snippet text, was_truncated boolean)
        Returns (None, False) if input text is None
    """
    if text is None:
        return None, False

    trimmed = text.strip()
    if not trimmed:
        return "", False

    # If text fits within limit, return as-is
    if len(trimmed) <= max_length:
        return trimmed, False

    # Truncate and optionally add ellipsis
    truncated = trimmed[:max_length].rstrip()
    if add_ellipsis:
        truncated += "..."

    return truncated, True


def extract_heading(text: Optional[str], fallback: str = "") -> str:
    """Extract first markdown heading from text, or return fallback.

    Simple extraction: finds first line starting with one or more '#' characters.

    Args:
        text: Text to search for headings
        fallback: Value to return if no heading found

    Returns:
        Extracted heading text (without '#' prefix) or fallback
    """
    if not text:
        return fallback

    lines = text.split('\n')
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('#'):
            # Remove leading '#' characters and whitespace
            heading = stripped.lstrip('#').strip()
            if heading:
                return heading

    return fallback


__all__ = ["generate_snippet", "extract_heading"]
