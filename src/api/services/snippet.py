"""Snippet generation utilities for search results."""

import re
from typing import Optional, Tuple


def generate_snippet(
    text: Optional[str],
    max_length: int = 320,
    add_ellipsis: bool = True,
    max_sentences: int = 2
) -> Tuple[Optional[str], bool]:
    """Generate a snippet from text content (sentence-aware).

    Attempts to extract up to max_sentences complete sentences, respecting
    max_length limit. Falls back to character truncation if sentence extraction
    would exceed the limit.

    Args:
        text: Source text to truncate
        max_length: Maximum snippet length in characters
        add_ellipsis: Whether to add "..." to truncated snippets
        max_sentences: Maximum number of sentences to include (default 2)

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

    # Attempt sentence-aware truncation
    # Split on sentence boundaries: . ! ? followed by space or end
    sentence_endings = re.finditer(r'[.!?](?:\s|$)', trimmed)

    sentences = []
    last_end = 0

    for match in sentence_endings:
        end_pos = match.end()
        sentence = trimmed[last_end:end_pos].strip()

        # Check if adding this sentence would exceed max_length
        potential_length = sum(len(s) + 1 for s in sentences) + len(sentence)

        if potential_length > max_length:
            break

        sentences.append(sentence)
        last_end = end_pos

        # Stop if we've reached max_sentences
        if len(sentences) >= max_sentences:
            break

    # If we got at least one complete sentence within limit, use it
    if sentences:
        snippet = " ".join(sentences)
        truncated = len(trimmed) > len(snippet)
        if add_ellipsis and truncated:
            snippet += "..."
        return snippet, truncated

    # Fallback: pure character truncation (no complete sentences fit)
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
