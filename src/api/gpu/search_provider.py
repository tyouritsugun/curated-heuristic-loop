"""GPU search provider module.

Contains the FAISS-based vector search provider used in GPU mode.
"""

import logging
from typing import Dict, List, Optional

import numpy as np
from sqlalchemy.orm import Session

from src.common.storage.schema import Experience, CategoryManual
from src.api.gpu.embedding_client import EmbeddingClient, EmbeddingClientError
from src.common.interfaces.search import SearchProvider, SearchProviderError
from src.common.interfaces.search_models import SearchResult, DuplicateCandidate, SearchReason
from src.api.gpu.faiss_manager import FAISSIndexManager, FAISSIndexError

logger = logging.getLogger(__name__)


def parse_two_phase_query(query: str) -> tuple[str, str]:
    """
    Parse a two-phase query into (search_phrase, full_context).

    Required format:
    - "[SEARCH] phrase [TASK] context"

    Returns:
        (search_phrase, full_query_for_reranking)

    Raises:
        ValueError: If query doesn't match required format or has empty parts.
                   Error message is designed to guide LLM to correct the format.

    Examples:
        >>> parse_two_phase_query("[SEARCH] auth patterns [TASK] Implement OAuth2")
        ('auth patterns', 'Implement OAuth2\\n\\nRelevant concepts: auth patterns')
    """
    # Check for required markers
    if "[SEARCH]" not in query:
        raise ValueError(
            "Query format error: Missing [SEARCH] marker.\n"
            "Required format: [SEARCH] <short keyword phrase> [TASK] <task description>\n"
            "Example: [SEARCH] authentication implementation patterns [TASK] Implement OAuth2 login\n"
            f"Your query: {query[:200]}"
        )

    if "[TASK]" not in query:
        raise ValueError(
            "Query format error: Missing [TASK] marker.\n"
            "Required format: [SEARCH] <short keyword phrase> [TASK] <task description>\n"
            "Example: [SEARCH] authentication implementation patterns [TASK] Implement OAuth2 login\n"
            f"Your query: {query[:200]}"
        )

    # Parse [SEARCH]/[TASK] format
    parts = query.split("[TASK]", 1)
    search = parts[0].replace("[SEARCH]", "").strip()
    task = parts[1].strip()

    # Validate: both parts must be non-empty
    if not search:
        raise ValueError(
            "Query format error: [SEARCH] phrase is empty.\n"
            "The SEARCH phrase should be 3-6 words combining [process] + [domain].\n"
            "Examples: 'migration planning', 'performance troubleshooting', 'API design'\n"
            f"Your query: {query[:200]}"
        )

    if not task:
        raise ValueError(
            "Query format error: [TASK] context is empty.\n"
            "The TASK should be one sentence describing your goal and constraints.\n"
            "Example: Implement secure OAuth2 login with refresh tokens\n"
            f"Your query: {query[:200]}"
        )

    # Construct full context for reranking
    full_context = f"{task}\n\nRelevant concepts: {search}"
    logger.debug("Parsed two-phase query: search=%r, task=%r", search, task)
    return (search, full_context)


class VectorFAISSProvider(SearchProvider):
    """Vector search provider using FAISS with optional reranking."""

    def __init__(
        self,
        index_manager: FAISSIndexManager,
        embedding_client: EmbeddingClient,
        model_name: str,
        reranker_client: Optional["RerankerClient"] = None,
        topk_retrieve: int = 100,
        topk_rerank: int = 40,
    ):
        self.index_manager = index_manager
        self.embedding_client = embedding_client
        self.model_name = model_name
        self.reranker_client = reranker_client
        self.topk_retrieve = topk_retrieve
        self.topk_rerank = min(topk_rerank, topk_retrieve)

    def search(
        self,
        session: Session,
        query: str,
        entity_type: Optional[str] = None,
        category_code: Optional[str] = None,
        top_k: int = 10,
    ) -> List[SearchResult]:
        """Search using vector similarity with two-phase query support."""
        try:
            # Parse query into two phases
            search_phrase, full_context = parse_two_phase_query(query)

            # Phase 1: FAISS with search phrase only
            try:
                query_embedding = self.embedding_client.encode_single(search_phrase)
            except EmbeddingClientError as exc:
                raise SearchProviderError(f"Failed to generate query embedding: {exc}") from exc

            try:
                scores, internal_ids = self.index_manager.search(
                    query_embedding=query_embedding,
                    top_k=self.topk_retrieve,
                    entity_type=entity_type,
                )
            except FAISSIndexError as exc:
                raise SearchProviderError(f"FAISS search failed: {exc}") from exc

            if len(internal_ids) == 0:
                return []

            entity_mappings: List[Dict[str, object]] = []
            for internal_id, score in zip(internal_ids, scores):
                mapping = self.index_manager.get_entity_id(int(internal_id))
                if mapping:
                    entity_mappings.append(
                        {
                            "entity_id": mapping["entity_id"],
                            "entity_type": mapping["entity_type"],
                            "score": float(score),
                        }
                    )

            # Phase 2: Reranking with full context
            if self.reranker_client and len(entity_mappings) > 1:
                entity_mappings = self._rerank_candidates(
                    session, full_context, entity_mappings[: self.topk_rerank]
                )

            if category_code:
                entity_mappings = self._filter_by_category(session, entity_mappings, category_code)

            entity_mappings = entity_mappings[:top_k]

            results: List[SearchResult] = []
            for rank, mapping in enumerate(entity_mappings):
                results.append(
                    SearchResult(
                        entity_id=str(mapping["entity_id"]),
                        entity_type=str(mapping["entity_type"]),
                        score=float(mapping["score"]),
                        reason=SearchReason.SEMANTIC_MATCH,
                        provider="vector_faiss",
                        rank=rank,
                    )
                )

            logger.info(
                "Vector search completed: original_query=%r, search_phrase=%r, entity_type=%s, category=%s, results=%s",
                query,
                search_phrase,
                entity_type,
                category_code,
                len(results),
            )

            return results
        except SearchProviderError:
            raise
        except Exception as exc:
            raise SearchProviderError(f"Vector search failed: {exc}") from exc

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
        """Find potential duplicates using vector similarity."""
        try:
            if entity_type == "experience":
                query_text = f"{title}\n\n{content}"
            else:
                query_text = content

            try:
                query_embedding = self.embedding_client.encode_single(query_text)
            except EmbeddingClientError as exc:
                raise SearchProviderError(f"Failed to generate embedding: {exc}") from exc

            try:
                scores, internal_ids = self.index_manager.search(
                    query_embedding=query_embedding,
                    top_k=self.topk_retrieve,
                    entity_type=entity_type,
                )
            except FAISSIndexError as exc:
                raise SearchProviderError(f"FAISS search failed: {exc}") from exc

            candidates: List[Dict[str, object]] = []
            for internal_id, score in zip(internal_ids, scores):
                if score < threshold:
                    continue
                mapping = self.index_manager.get_entity_id(int(internal_id))
                if not mapping:
                    continue
                if exclude_id and mapping["entity_id"] == exclude_id:
                    continue

                entity = self._fetch_entity(
                    session, mapping["entity_id"], mapping["entity_type"]
                )
                if not entity:
                    continue
                if category_code and getattr(entity, "category_code", None) != category_code:
                    continue

                if mapping["entity_type"] == "experience":
                    summary = entity.playbook[:200] if getattr(entity, "playbook", None) else None
                else:
                    summary = entity.summary or (
                        entity.content[:200] if getattr(entity, "content", None) else None
                    )

                candidates.append(
                    {
                        "entity_id": mapping["entity_id"],
                        "entity_type": mapping["entity_type"],
                        "score": float(score),
                        "title": entity.title,
                        "summary": summary,
                    }
                )

            if self.reranker_client and len(candidates) > 1:
                candidates = self._rerank_duplicates(
                    session, query_text, candidates[: self.topk_rerank]
                )

            results: List[DuplicateCandidate] = []
            for candidate in candidates:
                results.append(
                    DuplicateCandidate(
                        entity_id=str(candidate["entity_id"]),
                        entity_type=str(candidate["entity_type"]),
                        score=float(candidate["score"]),
                        reason=SearchReason.SEMANTIC_DUPLICATE,
                        provider="vector_faiss",
                        title=str(candidate["title"]),
                        summary=candidate["summary"],
                    )
                )

            logger.info(
                "Duplicate detection completed: title=%r, entity_type=%s, threshold=%s, candidates=%s",
                title,
                entity_type,
                threshold,
                len(results),
            )
            return results
        except SearchProviderError:
            raise
        except Exception as exc:
            raise SearchProviderError(f"Duplicate detection failed: {exc}") from exc

    def rebuild_index(self, session: Session) -> None:
        """Rebuild FAISS index from embeddings table."""
        try:
            from src.common.storage.repository import EmbeddingRepository
            from src.common.storage.schema import FAISSMetadata

            logger.info("Starting FAISS index rebuild")

            emb_repo = EmbeddingRepository(session)

            session.query(FAISSMetadata).delete()
            session.flush()

            self.index_manager._create_new_index()

            embeddings = emb_repo.get_all_by_model(self.model_name, entity_type=None)
            if not embeddings:
                logger.info("No embeddings found, index is empty")
                self.index_manager.save()
                return

            entity_ids: List[str] = []
            entity_types: List[str] = []
            embedding_vectors: List[np.ndarray] = []

            for emb in embeddings:
                entity_ids.append(emb.entity_id)
                entity_types.append(emb.entity_type)
                embedding_vectors.append(emb_repo.to_numpy(emb))

            embedding_array = np.vstack(embedding_vectors).astype(np.float32)

            self.index_manager.add(entity_ids, entity_types, embedding_array)
            self.index_manager.save()

            logger.info(
                "FAISS index rebuild completed: %s vectors indexed", len(entity_ids)
            )
        except Exception as exc:
            raise SearchProviderError(f"Index rebuild failed: {exc}") from exc

    def _rerank_candidates(
        self,
        session: Session,
        query: str,
        candidates: List[Dict[str, object]],
    ) -> List[Dict[str, object]]:
        if not self.reranker_client:
            return candidates

        try:
            texts: List[str] = []
            for candidate in candidates:
                entity = self._fetch_entity(
                    session, candidate["entity_id"], candidate["entity_type"]
                )
                if entity:
                    if candidate["entity_type"] == "experience":
                        text = f"{entity.title}\n\n{entity.playbook}"
                    else:
                        text = entity.content or entity.title
                    texts.append(text)
                else:
                    texts.append("")

            reranked_scores = self.reranker_client.rerank(query, texts)

            for candidate, new_score in zip(candidates, reranked_scores):
                candidate["score"] = new_score

            candidates.sort(key=lambda x: x["score"], reverse=True)
            return candidates
        except Exception as exc:
            logger.warning("Reranking failed, using FAISS scores: %s", exc)
            return candidates

    def _rerank_duplicates(
        self,
        session: Session,
        query_text: str,
        candidates: List[Dict[str, object]],
    ) -> List[Dict[str, object]]:
        if not self.reranker_client:
            return candidates

        try:
            texts: List[str] = []
            for candidate in candidates:
                entity = self._fetch_entity(
                    session, candidate["entity_id"], candidate["entity_type"]
                )
                if entity:
                    if candidate["entity_type"] == "experience":
                        text = f"{entity.title}\n\n{entity.playbook}"
                    else:
                        text = entity.content or entity.title
                    texts.append(text)
                else:
                    texts.append("")

            reranked_scores = self.reranker_client.rerank(query_text, texts)

            for candidate, new_score in zip(candidates, reranked_scores):
                candidate["score"] = new_score

            candidates.sort(key=lambda x: x["score"], reverse=True)
            return candidates
        except Exception as exc:
            logger.warning("Reranking failed, using FAISS scores: %s", exc)
            return candidates

    def _fetch_entity(self, session: Session, entity_id: str, entity_type: str):
        try:
            if entity_type == "experience":
                return (
                    session.query(Experience)
                    .filter(Experience.id == entity_id)
                    .first()
                )
            return (
                session.query(CategoryManual)
                .filter(CategoryManual.id == entity_id)
                .first()
            )
        except Exception as exc:
            logger.warning("Failed to fetch %s %s: %s", entity_type, entity_id, exc)
            return None

    def _filter_by_category(
        self,
        session: Session,
        mappings: List[Dict[str, object]],
        category_code: str,
    ) -> List[Dict[str, object]]:
        filtered: List[Dict[str, object]] = []
        for mapping in mappings:
            entity = self._fetch_entity(
                session, mapping["entity_id"], mapping["entity_type"]
            )
            if entity and getattr(entity, "category_code", None) == category_code:
                filtered.append(mapping)
        return filtered

    @property
    def name(self) -> str:
        return "vector_faiss"

    @property
    def is_available(self) -> bool:
        return self.index_manager.is_available


__all__ = ["VectorFAISSProvider"]
