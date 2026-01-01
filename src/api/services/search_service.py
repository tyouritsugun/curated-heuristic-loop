"""Search service orchestrator with provider resolution and fallback logic."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Any

from sqlalchemy.orm import Session

from src.common.interfaces.search import SearchProvider, SearchProviderError
from src.common.interfaces.search_models import SearchResult, DuplicateCandidate
from src.api.cpu.search_provider import SQLiteTextProvider

logger = logging.getLogger(__name__)


class SearchService:
    """Orchestrates search operations across multiple providers.

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
        """Initialize search service (sessionless).

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
            "SearchService initialized with primary=%s, fallback_enabled=%s, max_retries=%s",
            self.primary_provider_name,
            fallback_enabled,
            max_retries,
        )

    def _register_providers(self, vector_provider: Optional[SearchProvider] = None) -> None:
        """Register available search providers.

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
        """Return the registered vector provider if available."""
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
        """Search for entities matching query.

        Args:
            session: Request-scoped SQLAlchemy session
            query: Search query text
            entity_type: Filter by 'experience' or 'skill' (None for both)
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
                        "Provider %s is not available (attempt %s)",
                        self.primary_provider_name,
                        attempt + 1,
                    )
                    continue

                logger.debug(
                    "Searching with provider=%s, query=%r, entity_type=%s, category_code=%s, top_k=%s",
                    provider.name,
                    query,
                    entity_type,
                    category_code,
                    top_k,
                )

                results = provider.search(
                    session=session,
                    query=query,
                    entity_type=entity_type,
                    category_code=category_code,
                    top_k=top_k,
                )

                logger.info(
                    "Search completed: provider=%s, query=%r, results=%s",
                    provider.name,
                    query,
                    len(results),
                )

                return results

            except SearchProviderError as exc:
                logger.warning(
                    "Provider %s failed (attempt %s): %s",
                    self.primary_provider_name,
                    attempt + 1,
                    exc,
                )
                if attempt < self.max_retries:
                    continue  # Retry
                # Fall through to fallback

        # Fallback to SQLite text provider if enabled
        if self.fallback_enabled and self.primary_provider_name != "sqlite_text":
            logger.warning(
                "Falling back to sqlite_text provider after %s failed attempts",
                self.max_retries + 1,
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

                logger.info("Fallback search completed: results=%s", len(results))

                return results

            except Exception as exc:
                logger.error("Fallback provider also failed: %s", exc)
                raise SearchServiceError(f"All search providers failed: {exc}") from exc

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
        """Find potential duplicates of given content.

        Args:
            session: Request-scoped SQLAlchemy session
            title: Title of the entity to check
            content: Content of the entity (playbook for experiences, full content for skills)
            entity_type: 'experience' or 'skill'
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
                        "Provider %s is not available for duplicate detection",
                        self.primary_provider_name,
                    )
                    continue

                logger.debug(
                    "Finding duplicates with %s: title=%r, entity_type=%s, threshold=%s",
                    provider.name,
                    title,
                    entity_type,
                    threshold,
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
                    "Duplicate detection completed: provider=%s, candidates=%s",
                    provider.name,
                    len(candidates),
                )

                return candidates

            except SearchProviderError as exc:
                logger.warning(
                    "Duplicate detection failed with %s (attempt %s): %s",
                    self.primary_provider_name,
                    attempt + 1,
                    exc,
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

                logger.info(
                    "Fallback duplicate detection completed: candidates=%s",
                    len(candidates),
                )
                return candidates

            except Exception as exc:
                logger.error("Fallback duplicate detection failed: %s", exc)
                raise SearchServiceError(f"Duplicate detection failed: {exc}") from exc

        # No fallback
        raise SearchServiceError(
            f"Duplicate detection failed after {self.max_retries + 1} attempts"
        )

    def unified_search(
        self,
        session: Session,
        query: str,
        types: List[str],
        category_code: Optional[str] = None,
        limit: int = 10,
        offset: int = 0,
        min_score: Optional[float] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Unified search supporting multiple entity types with filtering.

        Args:
            session: Request-scoped SQLAlchemy session
            query: Search query text
            types: List of entity types to search ('experience', 'skill')
            category_code: Filter by category code (None for all)
            limit: Maximum number of results to return
            offset: Pagination offset
            min_score: Minimum relevance score (uses provider defaults if None)
            filters: AND-based filters (exact match): author, section

        Returns:
            Dict with keys:
                - results: List[SearchResult]
                - degraded: bool (whether fallback was used)
                - provider: str (provider that returned results)
                - warnings: List[str]
        """
        all_results: List[SearchResult] = []
        used_provider = self.primary_provider_name
        degraded = False
        warnings: List[str] = []

        # Search each entity type
        for entity_type in types:
            if entity_type not in ("experience", "skill"):
                warnings.append(f"Unsupported entity type '{entity_type}' ignored")
                continue

            try:
                # Search with sufficient headroom for filtering + pagination
                search_limit = limit + offset + 50  # Extra buffer for post-filtering
                type_results = self.search(
                    session=session,
                    query=query,
                    entity_type=entity_type,
                    category_code=category_code,
                    top_k=search_limit,
                )
                all_results.extend(type_results)

                # Track if we fell back to text search
                if type_results and type_results[0].provider == "sqlite_text" and self.primary_provider_name != "sqlite_text":
                    degraded = True
                    used_provider = "sqlite_text"

            except SearchServiceError as exc:
                logger.warning("Search failed for entity_type=%s: %s", entity_type, exc)
                warnings.append(f"Search failed for {entity_type}: {str(exc)}")
                continue

        # Apply post-search filters
        if filters:
            all_results = self._apply_filters(session, all_results, filters)

        # Sort by score (descending) and assign global ranks
        all_results.sort(key=lambda r: r.score or 0.0, reverse=True)
        for idx, result in enumerate(all_results):
            result.rank = idx

        # Apply min_score filtering
        if min_score is not None:
            before_count = len(all_results)
            all_results = [r for r in all_results if (r.score or 0.0) >= min_score]
            if before_count > len(all_results):
                warnings.append(
                    f"Filtered {before_count - len(all_results)} results below min_score={min_score}"
                )

        # Check if top result is below typical thresholds
        if all_results:
            top_score = all_results[0].score or 0.0
            default_threshold = 0.50 if used_provider == "vector_faiss" else 0.35
            if top_score < default_threshold:
                warnings.append(
                    f"Top result score ({top_score:.2f}) below recommended threshold ({default_threshold})"
                )

        # Apply pagination
        total_before_pagination = len(all_results)
        paginated_results = all_results[offset : offset + limit]

        return {
            "results": paginated_results,
            "total": total_before_pagination,
            "degraded": degraded,
            "provider": used_provider,
            "warnings": warnings,
        }

    def _apply_filters(
        self,
        session: Session,
        results: List[SearchResult],
        filters: Dict[str, Any],
    ) -> List[SearchResult]:
        """Apply AND-based filters to search results.

        Args:
            session: SQLAlchemy session
            results: Search results to filter
            filters: Dict with keys: author, section (null values ignored)

        Returns:
            Filtered list of results
        """
        from src.common.storage.repository import ExperienceRepository, CategorySkillRepository

        if not filters:
            return results

        # Extract filter values, ignoring None
        author_filter = filters.get("author")
        section_filter = filters.get("section")

        if not author_filter and not section_filter:
            return results

        filtered = []
        exp_repo = ExperienceRepository(session)
        skill_repo = CategorySkillRepository(session)

        for result in results:
            # Fetch entity to check filters
            if result.entity_type == "experience":
                entity = exp_repo.get_by_id(result.entity_id)
                if not entity:
                    continue

                # Apply filters (AND semantics, exact match)
                if author_filter and entity.author != author_filter:
                    continue
                if section_filter and entity.section != section_filter:
                    continue

                filtered.append(result)

            elif result.entity_type == "skill":
                entity = skill_repo.get_by_id(result.entity_id)
                if not entity:
                    continue

                # Skills only have author filter (no section)
                if author_filter and entity.author != author_filter:
                    continue

                filtered.append(result)

        return filtered

    def rebuild_index(self, session: Session, provider_name: Optional[str] = None) -> None:
        """Rebuild search index for specified provider.

        Args:
            session: Request-scoped SQLAlchemy session
            provider_name: Provider to rebuild (None for primary provider)

        Raises:
            SearchServiceError: If rebuild fails
        """
        target_provider = provider_name or self.primary_provider_name

        try:
            provider = self._get_provider(target_provider)
            logger.info("Rebuilding index for provider: %s", provider.name)

            provider.rebuild_index(session)

            logger.info("Index rebuild completed for provider: %s", provider.name)

        except Exception as exc:
            logger.error("Index rebuild failed for %s: %s", target_provider, exc)
            raise SearchServiceError(f"Index rebuild failed: {exc}") from exc

    def _get_provider(self, provider_name: str) -> SearchProvider:
        """Get provider by name.

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
        """Get list of available provider names."""
        return [name for name, provider in self._providers.items() if provider.is_available]


class SearchServiceError(Exception):
    """Exception raised by SearchService."""

    pass


__all__ = ["SearchService", "SearchServiceError"]
