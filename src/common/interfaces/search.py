"""Search provider protocol and errors."""

from __future__ import annotations

from typing import Protocol, List, Optional
from sqlalchemy.orm import Session

from .search_models import SearchResult, DuplicateCandidate


class SearchProviderError(Exception):
    """Base error for search providers."""


class SearchProvider(Protocol):
    """Abstract search provider interface."""

    @property
    def name(self) -> str: ...

    @property
    def is_available(self) -> bool: ...

    def search(
        self,
        session: Session,
        query: str,
        entity_type: Optional[str] = None,
        category_code: Optional[str] = None,
        top_k: int = 10,
    ) -> List[SearchResult]: ...

    def find_duplicates(
        self,
        session: Session,
        title: str,
        content: str,
        entity_type: str,
        category_code: Optional[str] = None,
        exclude_id: Optional[str] = None,
        threshold: Optional[float] = None,
    ) -> List[DuplicateCandidate]: ...

    def rebuild_index(self, session: Session) -> None: ...

