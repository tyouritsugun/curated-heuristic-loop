"""SQLite text search provider using LIKE-based matching"""
from typing import List, Optional
from sqlalchemy.orm import Session

from ..storage.schema import Experience, CategoryManual
from .provider import SearchProvider, SearchProviderError
from .models import SearchResult, DuplicateCandidate, SearchReason


class SQLiteTextProvider(SearchProvider):
    """Simple LIKE-based text search provider

    Uses SQLite LIKE operator for substring matching.
    Always available as fallback when vector search is unavailable.

    Characteristics:
    - Fast for small datasets (<10k entries)
    - No dependencies beyond SQLite
    - Limited semantic understanding (exact/substring match only)
    - Case-insensitive via SQLite LIKE

    Note: This provider is sessionless for thread-safety. All methods
    accept a session parameter rather than storing it as instance state.
    """

    def __init__(self):
        """Initialize SQLite text provider (sessionless)"""
        pass

    def search(
        self,
        session: Session,
        query: str,
        entity_type: Optional[str] = None,
        category_code: Optional[str] = None,
        top_k: int = 10,
    ) -> List[SearchResult]:
        """Search using LIKE-based text matching

        Searches in:
        - Experiences: title, playbook fields
        - Manuals: title, content, summary fields

        Args:
            query: Search query text
            entity_type: Filter by 'experience' or 'manual' (None for both)
            category_code: Filter by category code (None for all)
            top_k: Maximum results to return

        Returns:
            List of SearchResult ordered by updated_at DESC (most recent first)
        """
        results = []

        try:
            # Search experiences if requested
            if entity_type in (None, 'experience'):
                exp_results = self._search_experiences(session, query, category_code, top_k)
                results.extend(exp_results)

            # Search manuals if requested
            if entity_type in (None, 'manual'):
                manual_results = self._search_manuals(session, query, category_code, top_k)
                results.extend(manual_results)

            # Sort by updated_at DESC (most recent first)
            # Note: results is a list of tuples (entity, updated_at)
            results.sort(key=lambda x: x[1], reverse=True)

            # Convert to SearchResult objects with ranking
            search_results = []
            for rank, (entity, _) in enumerate(results[:top_k]):
                if isinstance(entity, Experience):
                    entity_id = entity.id
                    entity_type_str = 'experience'
                else:  # CategoryManual
                    entity_id = entity.id
                    entity_type_str = 'manual'

                search_results.append(SearchResult(
                    entity_id=entity_id,
                    entity_type=entity_type_str,
                    score=0.0,
                    reason=SearchReason.TEXT_MATCH,
                    provider="sqlite_text",
                    rank=rank,
                    degraded=True,
                    hint="Vector search unavailable; result generated via LIKE fallback.",
                ))

            return search_results

        except Exception as e:
            raise SearchProviderError(f"SQLite text search failed: {e}") from e

    def _search_experiences(
        self, session: Session, query: str, category_code: Optional[str], limit: int
    ) -> List[tuple]:
        """Search experiences using LIKE matching

        Returns:
            List of (experience, updated_at) tuples
        """
        pattern = f"%{query}%"

        q = session.query(Experience, Experience.updated_at).filter(
            (Experience.title.like(pattern)) | (Experience.playbook.like(pattern))
        )

        if category_code:
            q = q.filter(Experience.category_code == category_code)

        return q.limit(limit).all()

    def _search_manuals(
        self, session: Session, query: str, category_code: Optional[str], limit: int
    ) -> List[tuple]:
        """Search manuals using LIKE matching

        Returns:
            List of (manual, updated_at) tuples
        """
        pattern = f"%{query}%"

        q = session.query(CategoryManual, CategoryManual.updated_at).filter(
            (CategoryManual.title.like(pattern))
            | (CategoryManual.content.like(pattern))
            | (CategoryManual.summary.like(pattern))
        )

        if category_code:
            q = q.filter(CategoryManual.category_code == category_code)

        return q.limit(limit).all()

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
        """Find potential duplicates using exact title matching

        Text provider uses simple heuristics:
        1. Exact title match (case-insensitive)
        2. Title substring match
        3. Content substring match

        Args:
            title: Title to check for duplicates
            content: Content to check (playbook or manual content)
            entity_type: 'experience' or 'manual'
            category_code: Filter to category
            exclude_id: Exclude this ID from results
            threshold: Ignored for text provider (no scoring)

        Returns:
            List of DuplicateCandidate ordered by match quality
        """
        try:
            if entity_type == 'experience':
                return self._find_experience_duplicates(
                    session, title, content, category_code, exclude_id
                )
            elif entity_type == 'manual':
                return self._find_manual_duplicates(
                    session, title, content, category_code, exclude_id
                )
            else:
                raise ValueError(f"Invalid entity_type: {entity_type}")

        except Exception as e:
            raise SearchProviderError(f"Duplicate detection failed: {e}") from e

    def _find_experience_duplicates(
        self,
        session: Session,
        title: str,
        playbook: str,
        category_code: Optional[str],
        exclude_id: Optional[str],
    ) -> List[DuplicateCandidate]:
        """Find duplicate experiences using text matching"""
        candidates = []

        # 1. Exact title match (highest priority)
        q = session.query(Experience).filter(Experience.title.ilike(title))

        if category_code:
            q = q.filter(Experience.category_code == category_code)
        if exclude_id:
            q = q.filter(Experience.id != exclude_id)

        exact_matches = q.all()
        for exp in exact_matches:
            candidates.append(DuplicateCandidate(
                entity_id=exp.id,
                entity_type='experience',
                score=1.0,  # Exact title match
                reason=SearchReason.TEXT_DUPLICATE,
                provider="sqlite_text",
                title=exp.title,
                summary=exp.playbook[:200] if exp.playbook else None,
            ))

        # 2. Title substring match (if no exact matches)
        if not candidates:
            pattern = f"%{title}%"
            q = session.query(Experience).filter(Experience.title.like(pattern))

            if category_code:
                q = q.filter(Experience.category_code == category_code)
            if exclude_id:
                q = q.filter(Experience.id != exclude_id)

            substring_matches = q.limit(5).all()
            for exp in substring_matches:
                candidates.append(DuplicateCandidate(
                    entity_id=exp.id,
                    entity_type='experience',
                    score=0.75,  # Title substring match
                    reason=SearchReason.TEXT_DUPLICATE,
                    provider="sqlite_text",
                    title=exp.title,
                    summary=exp.playbook[:200] if exp.playbook else None,
                ))

        return candidates

    def _find_manual_duplicates(
        self,
        session: Session,
        title: str,
        content: str,
        category_code: Optional[str],
        exclude_id: Optional[str],
    ) -> List[DuplicateCandidate]:
        """Find duplicate manuals using text matching"""
        candidates = []

        # 1. Exact title match (highest priority)
        q = session.query(CategoryManual).filter(CategoryManual.title.ilike(title))

        if category_code:
            q = q.filter(CategoryManual.category_code == category_code)
        if exclude_id:
            q = q.filter(CategoryManual.id != exclude_id)

        exact_matches = q.all()
        for manual in exact_matches:
            candidates.append(DuplicateCandidate(
                entity_id=manual.id,
                entity_type='manual',
                score=1.0,  # Exact title match
                reason=SearchReason.TEXT_DUPLICATE,
                provider="sqlite_text",
                title=manual.title,
                summary=manual.summary or (manual.content[:200] if manual.content else None),
            ))

        # 2. Title substring match (if no exact matches)
        if not candidates:
            pattern = f"%{title}%"
            q = session.query(CategoryManual).filter(CategoryManual.title.like(pattern))

            if category_code:
                q = q.filter(CategoryManual.category_code == category_code)
            if exclude_id:
                q = q.filter(CategoryManual.id != exclude_id)

            substring_matches = q.limit(5).all()
            for manual in substring_matches:
                candidates.append(DuplicateCandidate(
                    entity_id=manual.id,
                    entity_type='manual',
                    score=0.75,  # Title substring match
                    reason=SearchReason.TEXT_DUPLICATE,
                    provider="sqlite_text",
                    title=manual.title,
                    summary=manual.summary or (manual.content[:200] if manual.content else None),
                ))

        return candidates

    def rebuild_index(self, session: Session) -> None:
        """No-op for text provider (no index to rebuild)"""
        pass

    @property
    def name(self) -> str:
        return "sqlite_text"

    @property
    def is_available(self) -> bool:
        """Text provider is always available"""
        return True
