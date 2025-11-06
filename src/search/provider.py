"""Abstract search provider interface"""
from abc import ABC, abstractmethod
from typing import List, Optional

from sqlalchemy.orm import Session

from .models import SearchResult, DuplicateCandidate


class SearchProvider(ABC):
    """Abstract base class for search providers

    Implementations:
    - SQLiteTextProvider: Simple LIKE-based text search (fallback)
    - VectorFAISSProvider: Semantic vector search with optional reranking

    Note: Providers must be sessionless - they accept a session parameter
    for each operation to ensure thread-safety in multi-threaded environments.
    """

    @abstractmethod
    def search(
        self,
        session: Session,
        query: str,
        entity_type: Optional[str] = None,
        category_code: Optional[str] = None,
        top_k: int = 10,
    ) -> List[SearchResult]:
        """Search for entities matching query

        Args:
            session: Request-scoped SQLAlchemy session
            query: Search query text
            entity_type: Filter by 'experience' or 'manual' (None for both)
            category_code: Filter by category code (None for all categories)
            top_k: Maximum number of results to return

        Returns:
            List of SearchResult ordered by relevance (best first)

        Raises:
            SearchProviderError: If search fails
        """
        pass

    @abstractmethod
    def find_duplicates(
        self,
        session: Session,
        title: str,
        content: str,
        entity_type: str,
        category_code: Optional[str] = None,
        exclude_id: Optional[str] = None,
        threshold: float = 0.60,
    ) -> List[DuplicateCandidate]:
        """Find potential duplicates of given content

        Args:
            session: Request-scoped SQLAlchemy session
            title: Title of the entity to check
            content: Content of the entity (playbook for experiences, full content for manuals)
            entity_type: 'experience' or 'manual'
            category_code: Filter to category (None for all categories)
            exclude_id: Exclude this ID from results (for updates)
            threshold: Minimum similarity score to consider (0.0-1.0)

        Returns:
            List of DuplicateCandidate ordered by similarity (highest first)

        Raises:
            SearchProviderError: If duplicate detection fails
        """
        pass

    @abstractmethod
    def rebuild_index(self, session: Session) -> None:
        """Rebuild search index from scratch

        For text provider: No-op (no index to rebuild)
        For vector provider: Regenerate FAISS index from embeddings table

        Args:
            session: Request-scoped SQLAlchemy session

        Raises:
            SearchProviderError: If rebuild fails
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name for logging and metadata"""
        pass

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Check if provider is available and ready to use

        Returns:
            True if provider can handle queries, False otherwise
        """
        pass


class SearchProviderError(Exception):
    """Base exception for search provider errors"""
    pass
