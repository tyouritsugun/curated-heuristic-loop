"""Search-related DTOs shared across API implementations."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SearchReason(str, Enum):
    """Reason codes for how a result was found."""

    ID_LOOKUP = "id_lookup"  # Direct ID lookup
    SEMANTIC_MATCH = "semantic_match"  # Vector similarity match
    TEXT_MATCH = "text_match"  # Text-based LIKE match
    SEMANTIC_DUPLICATE = "semantic_duplicate"  # Duplicate detection via vector similarity
    TEXT_DUPLICATE = "text_duplicate"  # Duplicate detection via text matching


@dataclass
class SearchResult:
    """Result from search operation.

    Attributes:
        entity_id: Experience or manual ID
        entity_type: 'experience' or 'manual'
        score: Relevance score (0.0-1.0, higher is better, None if not applicable)
        reason: How this result was found
        provider: Which provider returned this result ('vector_faiss', 'sqlite_text', 'direct')
        rank: Position in results (0-indexed)
        degraded: Whether the provider is in a degraded (fallback) mode
        hint: Optional guidance for handling degraded results
    """

    entity_id: str
    entity_type: str  # 'experience' or 'manual'
    score: Optional[float] = None
    reason: SearchReason = SearchReason.ID_LOOKUP
    provider: str = "direct"
    rank: int = 0
    degraded: bool = False
    hint: Optional[str] = None

    def __post_init__(self):
        """Validate fields."""
        if self.entity_type not in ("experience", "manual"):
            raise ValueError(f"Invalid entity_type: {self.entity_type}")

        if self.score is not None and not (0.0 <= self.score <= 1.0):
            raise ValueError(f"Score must be in [0.0, 1.0], got {self.score}")


@dataclass
class DuplicateCandidate:
    """Potential duplicate found during write operation.

    Attributes:
        entity_id: ID of existing entity that might be duplicate
        entity_type: 'experience' or 'manual'
        score: Similarity score (0.0-1.0, higher means more similar)
        reason: How duplicate was detected
        provider: Which provider found this duplicate
        title: Title of the existing entity (for display)
        summary: Brief summary or playbook excerpt (for context)
    """

    entity_id: str
    entity_type: str
    score: float
    reason: SearchReason
    provider: str
    title: str
    summary: Optional[str] = None

    def __post_init__(self):
        """Validate fields."""
        if self.entity_type not in ("experience", "manual"):
            raise ValueError(f"Invalid entity_type: {self.entity_type}")

        if not (0.0 <= self.score <= 1.0):
            raise ValueError(f"Score must be in [0.0, 1.0], got {self.score}")


__all__ = ["SearchReason", "SearchResult", "DuplicateCandidate"]
