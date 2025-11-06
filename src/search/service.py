"""Search service orchestrator with provider resolution and fallback logic"""
import logging
from typing import List, Optional, Dict
from sqlalchemy.orm import Session

from .provider import SearchProvider, SearchProviderError
from .models import SearchResult, DuplicateCandidate
from .sqlite_provider import SQLiteTextProvider

logger = logging.getLogger(__name__)


class SearchService:
    """Orchestrates search operations across multiple providers

    Responsibilities:
    - Provider resolution based on configuration
    - Automatic fallback when primary provider fails
    - Retry logic with configurable attempts
    - Response normalization

    Vector search is optional; when unavailable the service falls back to SQLite text search.

    Note: This service is sessionless for thread-safety. All methods accept
    a session parameter rather than storing it as instance state.
    """

    def __init__(
        self,
        primary_provider: Optional[str] = None,
        fallback_enabled: bool = True,
        max_retries: int = 1,
        vector_provider: Optional[SearchProvider] = None,
    ):
        """Initialize search service (sessionless)

        Args:
            primary_provider: Provider name ('sqlite_text' or 'vector_faiss')
                             None defaults to 'vector_faiss' if available, else 'sqlite_text'
            fallback_enabled: Enable automatic fallback to text search
            max_retries: Number of retries before falling back (default: 1)
            vector_provider: Optional VectorFAISSProvider instance (None to disable)
        """
        self.fallback_enabled = fallback_enabled
        self.max_retries = max_retries

        # Initialize provider registry
        self._providers: Dict[str, SearchProvider] = {}
        self._register_providers(vector_provider)

        # Set primary provider
        # Default to vector_faiss if available, else sqlite_text
        if primary_provider is None:
            if "vector_faiss" in self._providers and self._providers["vector_faiss"].is_available:
                self.primary_provider_name = "vector_faiss"
            else:
                self.primary_provider_name = "sqlite_text"
        else:
            self.primary_provider_name = primary_provider

        # Validate primary provider exists
        if self.primary_provider_name not in self._providers:
            raise ValueError(
                f"Unknown primary provider: {self.primary_provider_name}. "
                f"Available: {list(self._providers.keys())}"
            )

        logger.info(
            f"SearchService initialized with primary={self.primary_provider_name}, "
            f"fallback_enabled={fallback_enabled}, max_retries={max_retries}"
        )

    def _register_providers(self, vector_provider: Optional[SearchProvider] = None) -> None:
        """Register available search providers

        Args:
            vector_provider: Optional VectorFAISSProvider instance
        """
        # Always register SQLite text provider (always available)
        self._providers["sqlite_text"] = SQLiteTextProvider()

        # Register vector provider if provided and available
        if vector_provider and vector_provider.is_available:
            self._providers["vector_faiss"] = vector_provider
            logger.info("Vector FAISS provider registered and available")

    def get_vector_provider(self) -> Optional[SearchProvider]:
        """Return the registered vector provider if available"""
        provider = self._providers.get("vector_faiss")
        if provider and provider.is_available:
            return provider
        return None

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
            category_code: Filter by category code (None for all)
            top_k: Maximum number of results to return

        Returns:
            List of SearchResult ordered by relevance

        Raises:
            SearchServiceError: If all providers fail
        """
        # Try primary provider with retries
        for attempt in range(self.max_retries + 1):
            try:
                provider = self._get_provider(self.primary_provider_name)

                if not provider.is_available:
                    logger.warning(
                        f"Provider {self.primary_provider_name} is not available (attempt {attempt + 1})"
                    )
                    continue

                logger.debug(
                    f"Searching with {provider.name}: query='{query}', "
                    f"entity_type={entity_type}, category={category_code}, top_k={top_k}"
                )

                results = provider.search(
                    session=session,
                    query=query,
                    entity_type=entity_type,
                    category_code=category_code,
                    top_k=top_k,
                )

                logger.info(
                    f"Search completed: provider={provider.name}, "
                    f"query='{query}', results={len(results)}"
                )

                return results

            except SearchProviderError as e:
                logger.warning(
                    f"Provider {self.primary_provider_name} failed (attempt {attempt + 1}): {e}"
                )
                if attempt < self.max_retries:
                    continue  # Retry
                # Fall through to fallback

        # Fallback to SQLite text provider if enabled
        if self.fallback_enabled and self.primary_provider_name != "sqlite_text":
            logger.warning(
                f"Falling back to sqlite_text provider after {self.max_retries + 1} failed attempts"
            )
            try:
                fallback_provider = self._providers["sqlite_text"]
                results = fallback_provider.search(
                    session=session,
                    query=query,
                    entity_type=entity_type,
                    category_code=category_code,
                    top_k=top_k,
                )

                logger.info(
                    f"Fallback search completed: results={len(results)}"
                )

                return results

            except Exception as e:
                logger.error(f"Fallback provider also failed: {e}")
                raise SearchServiceError(f"All search providers failed: {e}") from e

        # No fallback or primary was already sqlite_text
        raise SearchServiceError(
            f"Search failed after {self.max_retries + 1} attempts with {self.primary_provider_name}"
        )

    def find_duplicates(
        self,
        session: Session,
        title: str,
        content: str,
        entity_type: str,
        category_code: Optional[str] = None,
        exclude_id: Optional[str] = None,
        threshold: Optional[float] = None,
    ) -> List[DuplicateCandidate]:
        """Find potential duplicates of given content

        Args:
            session: Request-scoped SQLAlchemy session
            title: Title of the entity to check
            content: Content of the entity (playbook for experiences, full content for manuals)
            entity_type: 'experience' or 'manual'
            category_code: Filter to category (None for all)
            exclude_id: Exclude this ID from results (for updates)
            threshold: Minimum similarity score (provider-specific defaults if None)

        Returns:
            List of DuplicateCandidate ordered by similarity (highest first)

        Raises:
            SearchServiceError: If duplicate detection fails
        """
        # Use provider-specific default thresholds if not specified
        if threshold is None:
            # Uses config-based thresholds (0.85 for updates, 0.60 for inserts)
            threshold = 0.60

        # Try primary provider with retries
        for attempt in range(self.max_retries + 1):
            try:
                provider = self._get_provider(self.primary_provider_name)

                if not provider.is_available:
                    logger.warning(
                        f"Provider {self.primary_provider_name} is not available for duplicate detection"
                    )
                    continue

                logger.debug(
                    f"Finding duplicates with {provider.name}: "
                    f"title='{title}', entity_type={entity_type}, threshold={threshold}"
                )

                candidates = provider.find_duplicates(
                    session=session,
                    title=title,
                    content=content,
                    entity_type=entity_type,
                    category_code=category_code,
                    exclude_id=exclude_id,
                    threshold=threshold,
                )

                logger.info(
                    f"Duplicate detection completed: provider={provider.name}, "
                    f"candidates={len(candidates)}"
                )

                return candidates

            except SearchProviderError as e:
                logger.warning(
                    f"Duplicate detection failed with {self.primary_provider_name} (attempt {attempt + 1}): {e}"
                )
                if attempt < self.max_retries:
                    continue  # Retry

        # Fallback to SQLite text provider if enabled
        if self.fallback_enabled and self.primary_provider_name != "sqlite_text":
            logger.warning("Falling back to sqlite_text for duplicate detection")
            try:
                fallback_provider = self._providers["sqlite_text"]
                candidates = fallback_provider.find_duplicates(
                    session=session,
                    title=title,
                    content=content,
                    entity_type=entity_type,
                    category_code=category_code,
                    exclude_id=exclude_id,
                    threshold=threshold,
                )

                logger.info(f"Fallback duplicate detection completed: candidates={len(candidates)}")
                return candidates

            except Exception as e:
                logger.error(f"Fallback duplicate detection failed: {e}")
                raise SearchServiceError(f"Duplicate detection failed: {e}") from e

        # No fallback
        raise SearchServiceError(
            f"Duplicate detection failed after {self.max_retries + 1} attempts"
        )

    def rebuild_index(self, session: Session, provider_name: Optional[str] = None) -> None:
        """Rebuild search index for specified provider

        Args:
            session: Request-scoped SQLAlchemy session
            provider_name: Provider to rebuild (None for primary provider)

        Raises:
            SearchServiceError: If rebuild fails
        """
        target_provider = provider_name or self.primary_provider_name

        try:
            provider = self._get_provider(target_provider)
            logger.info(f"Rebuilding index for provider: {provider.name}")

            provider.rebuild_index(session)

            logger.info(f"Index rebuild completed for provider: {provider.name}")

        except Exception as e:
            logger.error(f"Index rebuild failed for {target_provider}: {e}")
            raise SearchServiceError(f"Index rebuild failed: {e}") from e

    def _get_provider(self, provider_name: str) -> SearchProvider:
        """Get provider by name

        Args:
            provider_name: Name of the provider

        Returns:
            SearchProvider instance

        Raises:
            ValueError: If provider not found
        """
        provider = self._providers.get(provider_name)
        if not provider:
            raise ValueError(
                f"Provider not found: {provider_name}. "
                f"Available: {list(self._providers.keys())}"
            )
        return provider

    @property
    def available_providers(self) -> List[str]:
        """Get list of available provider names"""
        return [
            name for name, provider in self._providers.items()
            if provider.is_available
        ]


class SearchServiceError(Exception):
    """Exception raised by SearchService"""
    pass
