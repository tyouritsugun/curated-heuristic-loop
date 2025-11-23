"""CPU search provider module.

Contains the SQLite text search provider used in CPU mode.
"""

from typing import List, Optional
import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.common.storage.schema import Experience, CategoryManual
from src.common.interfaces.search import SearchProvider, SearchProviderError
from src.common.interfaces.search_models import SearchResult, DuplicateCandidate, SearchReason


class SQLiteTextProvider(SearchProvider):
    """Simple LIKE-based text search provider.

    Uses SQLite LIKE operator for substring matching.
    Always available as fallback when vector search is unavailable.
    """

    def __init__(self) -> None:
        """Initialize SQLite text provider (sessionless)."""
        self._name = "sqlite_text"

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_available(self) -> bool:
        # SQLite provider is always available because it relies only on the DB.
        return True

    def search(
        self,
        session: Session,
        query: str,
        entity_type: Optional[str] = None,
        category_code: Optional[str] = None,
        top_k: int = 10,
    ) -> List[SearchResult]:
        """Search using LIKE-based text matching."""
        results: List[tuple] = []

        try:
            if entity_type in (None, "experience"):
                results.extend(self._search_experiences(session, query, category_code, top_k))

            if entity_type in (None, "manual"):
                results.extend(self._search_manuals(session, query, category_code, top_k))

            # Sort by updated_at DESC (most recent first)
            results.sort(key=lambda x: x[1], reverse=True)

            search_results: List[SearchResult] = []
            for rank, (entity, _) in enumerate(results[:top_k]):
                if isinstance(entity, Experience):
                    entity_id = entity.id
                    entity_type_str = "experience"
                else:
                    entity_id = entity.id
                    entity_type_str = "manual"

                search_results.append(
                    SearchResult(
                        entity_id=entity_id,
                        entity_type=entity_type_str,
                        score=0.0,
                        reason=SearchReason.TEXT_MATCH,
                        provider="sqlite_text",
                        rank=rank,
                        degraded=True,
                        hint="Vector search unavailable; result generated via LIKE fallback.",
                    )
                )

            return search_results
        except Exception as exc:
            raise SearchProviderError(f"SQLite text search failed: {exc}") from exc

    def _tokenize(self, query: str) -> List[str]:
        tokens = [token.strip() for token in re.split(r"[\s,]+", query) if token.strip()]
        return tokens[:5]

    def _search_experiences(
        self,
        session: Session,
        query: str,
        category_code: Optional[str],
        limit: int,
    ) -> List[tuple]:
        """Search experiences using LIKE matching."""
        pattern = f"%{query}%"
        tokens = self._tokenize(query)

        filters = [or_(Experience.title.like(pattern), Experience.playbook.like(pattern))]
        for token in tokens:
            token_pattern = f"%{token}%"
            filters.append(
                or_(Experience.title.ilike(token_pattern), Experience.playbook.ilike(token_pattern))
            )

        q = session.query(Experience, Experience.updated_at).filter(or_(*filters))

        if category_code:
            q = q.filter(Experience.category_code == category_code)

        return q.limit(limit).all()

    def _search_manuals(
        self,
        session: Session,
        query: str,
        category_code: Optional[str],
        limit: int,
    ) -> List[tuple]:
        """Search manuals using LIKE matching."""
        pattern = f"%{query}%"
        tokens = self._tokenize(query)

        filters = [
            or_(
                CategoryManual.title.like(pattern),
                CategoryManual.content.like(pattern),
                CategoryManual.summary.like(pattern),
            )
        ]
        for token in tokens:
            token_pattern = f"%{token}%"
            filters.append(
                or_(
                    CategoryManual.title.ilike(token_pattern),
                    CategoryManual.content.ilike(token_pattern),
                    CategoryManual.summary.ilike(token_pattern),
                )
            )

        q = session.query(CategoryManual, CategoryManual.updated_at).filter(or_(*filters))

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
        """Find potential duplicates using simple text heuristics."""
        del threshold  # unused for text provider
        try:
            if entity_type == "experience":
                return self._find_experience_duplicates(
                    session, title, content, category_code, exclude_id
                )
            if entity_type == "manual":
                return self._find_manual_duplicates(
                    session, title, content, category_code, exclude_id
                )
            raise ValueError(f"Invalid entity_type: {entity_type}")
        except Exception as exc:
            raise SearchProviderError(f"Duplicate detection failed: {exc}") from exc

    def rebuild_index(self, session: Session) -> None:  # noqa: D401 - interface compliance
        """No-op for SQLite provider (no separate index files)."""
        del session
        return None

    def _find_experience_duplicates(
        self,
        session: Session,
        title: str,
        playbook: str,
        category_code: Optional[str],
        exclude_id: Optional[str],
    ) -> List[DuplicateCandidate]:
        candidates: List[DuplicateCandidate] = []

        # 1. Exact title match
        q = session.query(Experience).filter(Experience.title.ilike(title))
        if category_code:
            q = q.filter(Experience.category_code == category_code)
        if exclude_id:
            q = q.filter(Experience.id != exclude_id)

        for exp in q.all():
            candidates.append(
                DuplicateCandidate(
                    entity_id=exp.id,
                    entity_type="experience",
                    score=1.0,
                    reason=SearchReason.TEXT_DUPLICATE,
                    provider="sqlite_text",
                    title=exp.title,
                    summary=exp.playbook[:200] if exp.playbook else None,
                )
            )

        # 2. Title substring match if no exact matches
        if not candidates:
            pattern = f"%{title}%"
            q = session.query(Experience).filter(Experience.title.like(pattern))
            if category_code:
                q = q.filter(Experience.category_code == category_code)
            if exclude_id:
                q = q.filter(Experience.id != exclude_id)

            for exp in q.limit(5).all():
                candidates.append(
                    DuplicateCandidate(
                        entity_id=exp.id,
                        entity_type="experience",
                        score=0.75,
                        reason=SearchReason.TEXT_DUPLICATE,
                        provider="sqlite_text",
                        title=exp.title,
                        summary=exp.playbook[:200] if exp.playbook else None,
                    )
                )

        return candidates

    def _find_manual_duplicates(
        self,
        session: Session,
        title: str,
        content: str,
        category_code: Optional[str],
        exclude_id: Optional[str],
    ) -> List[DuplicateCandidate]:
        candidates: List[DuplicateCandidate] = []

        q = session.query(CategoryManual).filter(CategoryManual.title.ilike(title))
        if category_code:
            q = q.filter(CategoryManual.category_code == category_code)
        if exclude_id:
            q = q.filter(CategoryManual.id != exclude_id)

        for manual in q.all():
            candidates.append(
                DuplicateCandidate(
                    entity_id=manual.id,
                    entity_type="manual",
                    score=1.0,
                    reason=SearchReason.TEXT_DUPLICATE,
                    provider="sqlite_text",
                    title=manual.title,
                    summary=manual.summary or (manual.content[:200] if manual.content else None),
                )
            )

        if not candidates:
            pattern = f"%{title}%"
            q = session.query(CategoryManual).filter(CategoryManual.title.like(pattern))
            if category_code:
                q = q.filter(CategoryManual.category_code == category_code)
            if exclude_id:
                q = q.filter(CategoryManual.id != exclude_id)

            for manual in q.limit(5).all():
                candidates.append(
                    DuplicateCandidate(
                        entity_id=manual.id,
                        entity_type="manual",
                        score=0.75,
                        reason=SearchReason.TEXT_DUPLICATE,
                        provider="sqlite_text",
                        title=manual.title,
                        summary=manual.summary or (manual.content[:200] if manual.content else None),
                    )
                )

        return candidates

    def rebuild_index(self, session: Session) -> None:  # noqa: ARG002
        """No-op for text provider (no index to rebuild)."""
        return None

    @property
    def name(self) -> str:
        return "sqlite_text"

    @property
    def is_available(self) -> bool:
        """Text provider is always available."""
        return True


__all__ = ["SQLiteTextProvider"]
