"""Vector-based search provider using FAISS and optional reranking"""
import logging
from typing import List, Optional, Dict
import numpy as np

from sqlalchemy.orm import Session

from ..storage.schema import Experience, CategoryManual
from ..embedding.client import EmbeddingClient, EmbeddingClientError
from .provider import SearchProvider, SearchProviderError
from .models import SearchResult, DuplicateCandidate, SearchReason
from .faiss_index import FAISSIndexManager, FAISSIndexError

logger = logging.getLogger(__name__)


class VectorFAISSProvider(SearchProvider):
    """Vector search provider using FAISS with optional reranking

    Pipeline:
    1. Generate query embedding
    2. Retrieve top-k candidates from FAISS (cosine similarity)
    3. [Optional] Rerank candidates using cross-encoder
    4. Fetch full entity data from SQLite
    5. Return scored results

    Always available: False (depends on FAISS + embedding model)

    Note: This provider is sessionless for thread-safety. All methods
    accept a session parameter rather than storing it as instance state.
    """

    def __init__(
        self,
        index_manager: FAISSIndexManager,
        embedding_client: EmbeddingClient,
        model_name: str,
        reranker_client: Optional['RerankerClient'] = None,
        topk_retrieve: int = 100,
        topk_rerank: int = 40,
    ):
        """Initialize vector provider (sessionless)

        Args:
            index_manager: FAISS index manager
            embedding_client: Client for generating embeddings
            model_name: Full model name in 'repo:quant' format (from config.embedding_model)
            reranker_client: Optional reranker for precision (None to disable)
            topk_retrieve: Number of FAISS candidates to retrieve
            topk_rerank: Number of candidates to rerank (must be <= topk_retrieve)
        """
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
        """Search using vector similarity

        Args:
            query: Search query text
            entity_type: Filter by 'experience' or 'manual' (None for both)
            category_code: Filter by category code (None for all)
            top_k: Maximum results to return

        Returns:
            List of SearchResult ordered by relevance (highest score first)

        Raises:
            SearchProviderError: If search fails
        """
        try:
            # Generate query embedding
            try:
                query_embedding = self.embedding_client.encode_single(query)
            except EmbeddingClientError as e:
                raise SearchProviderError(f"Failed to generate query embedding: {e}") from e

            # Retrieve candidates from FAISS
            try:
                scores, internal_ids = self.index_manager.search(
                    query_embedding=query_embedding,
                    top_k=self.topk_retrieve,
                    entity_type=entity_type
                )
            except FAISSIndexError as e:
                raise SearchProviderError(f"FAISS search failed: {e}") from e

            if len(internal_ids) == 0:
                return []

            # Map internal IDs to entity IDs
            entity_mappings = []
            for internal_id, score in zip(internal_ids, scores):
                mapping = self.index_manager.get_entity_id(int(internal_id))
                if mapping:
                    entity_mappings.append({
                        'entity_id': mapping['entity_id'],
                        'entity_type': mapping['entity_type'],
                        'score': float(score)
                    })

            # Apply reranking if enabled
            if self.reranker_client and len(entity_mappings) > 1:
                entity_mappings = self._rerank_candidates(
                    session,
                    query,
                    entity_mappings[:self.topk_rerank]
                )

            # Filter by category if specified
            if category_code:
                entity_mappings = self._filter_by_category(session, entity_mappings, category_code)

            # Take top_k results
            entity_mappings = entity_mappings[:top_k]

            # Convert to SearchResult objects
            results = []
            for rank, mapping in enumerate(entity_mappings):
                results.append(SearchResult(
                    entity_id=mapping['entity_id'],
                    entity_type=mapping['entity_type'],
                    score=mapping['score'],
                    reason=SearchReason.SEMANTIC_MATCH,
                    provider="vector_faiss",
                    rank=rank
                ))

            logger.info(
                f"Vector search completed: query='{query}', "
                f"entity_type={entity_type}, category={category_code}, "
                f"results={len(results)}"
            )

            return results

        except SearchProviderError:
            raise
        except Exception as e:
            raise SearchProviderError(f"Vector search failed: {e}") from e

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
        """Find potential duplicates using vector similarity

        Args:
            session: Request-scoped SQLAlchemy session
            title: Title to check
            content: Content to check (playbook or manual content)
            entity_type: 'experience' or 'manual'
            category_code: Filter to category (None for all)
            exclude_id: Exclude this ID from results
            threshold: Minimum similarity score (0.0-1.0)

        Returns:
            List of DuplicateCandidate ordered by similarity (highest first)

        Raises:
            SearchProviderError: If duplicate detection fails
        """
        try:
            # Combine title + content for embedding (matches embedding content strategy)
            if entity_type == 'experience':
                query_text = f"{title}\n\n{content}"
            else:  # manual
                query_text = content  # Full content

            # Generate query embedding
            try:
                query_embedding = self.embedding_client.encode_single(query_text)
            except EmbeddingClientError as e:
                raise SearchProviderError(f"Failed to generate embedding: {e}") from e

            # Retrieve candidates from FAISS
            try:
                scores, internal_ids = self.index_manager.search(
                    query_embedding=query_embedding,
                    top_k=self.topk_retrieve,
                    entity_type=entity_type
                )
            except FAISSIndexError as e:
                raise SearchProviderError(f"FAISS search failed: {e}") from e

            # Map internal IDs to entity IDs and filter by threshold
            candidates = []
            for internal_id, score in zip(internal_ids, scores):
                # Filter by threshold
                if score < threshold:
                    continue

                mapping = self.index_manager.get_entity_id(int(internal_id))
                if not mapping:
                    continue

                # Skip excluded ID
                if exclude_id and mapping['entity_id'] == exclude_id:
                    continue

                # Filter by category if specified
                if category_code:
                    entity = self._fetch_entity(session, mapping['entity_id'], mapping['entity_type'])
                    if not entity or entity.category_code != category_code:
                        continue
                else:
                    entity = self._fetch_entity(session, mapping['entity_id'], mapping['entity_type'])

                if not entity:
                    continue

                # Create candidate
                if mapping['entity_type'] == 'experience':
                    summary = entity.playbook[:200] if entity.playbook else None
                else:  # manual
                    summary = entity.summary or (entity.content[:200] if entity.content else None)

                candidates.append({
                    'entity_id': mapping['entity_id'],
                    'entity_type': mapping['entity_type'],
                    'score': float(score),
                    'title': entity.title,
                    'summary': summary
                })

            # Apply reranking if enabled
            if self.reranker_client and len(candidates) > 1:
                candidates = self._rerank_duplicates(session, query_text, candidates[:self.topk_rerank])

            # Convert to DuplicateCandidate objects
            results = []
            for candidate in candidates:
                results.append(DuplicateCandidate(
                    entity_id=candidate['entity_id'],
                    entity_type=candidate['entity_type'],
                    score=candidate['score'],
                    reason=SearchReason.SEMANTIC_DUPLICATE,
                    provider="vector_faiss",
                    title=candidate['title'],
                    summary=candidate['summary']
                ))

            logger.info(
                f"Duplicate detection completed: title='{title}', "
                f"entity_type={entity_type}, threshold={threshold}, "
                f"candidates={len(results)}"
            )

            return results

        except SearchProviderError:
            raise
        except Exception as e:
            raise SearchProviderError(f"Duplicate detection failed: {e}") from e

    def rebuild_index(self, session: Session) -> None:
        """Rebuild FAISS index from embeddings table

        This will:
        1. Clear existing FAISS index
        2. Clear faiss_metadata table
        3. Load all embeddings from database
        4. Rebuild index and metadata

        Args:
            session: Request-scoped SQLAlchemy session

        Raises:
            SearchProviderError: If rebuild fails
        """
        try:
            from ..storage.repository import EmbeddingRepository
            from ..storage.schema import FAISSMetadata

            logger.info("Starting FAISS index rebuild")

            # Get embedding repository
            emb_repo = EmbeddingRepository(session)

            # Clear existing metadata
            session.query(FAISSMetadata).delete()
            session.flush()

            # Create new empty index
            self.index_manager._create_new_index()

            # Get all embeddings for this model (use full model name with quantization)
            embeddings = emb_repo.get_all_by_model(
                self.model_name,
                entity_type=None  # Both experiences and manuals
            )

            if not embeddings:
                logger.info("No embeddings found, index is empty")
                self.index_manager.save()
                return

            # Prepare data for bulk add
            entity_ids = []
            entity_types = []
            embedding_vectors = []

            for emb in embeddings:
                entity_ids.append(emb.entity_id)
                entity_types.append(emb.entity_type)
                embedding_vectors.append(emb_repo.to_numpy(emb))

            # Convert to numpy array
            embedding_array = np.vstack(embedding_vectors).astype(np.float32)

            # Add to FAISS
            self.index_manager.add(entity_ids, entity_types, embedding_array)

            # Save index
            self.index_manager.save()

            logger.info(
                f"FAISS index rebuild completed: {len(entity_ids)} vectors indexed"
            )

        except Exception as e:
            raise SearchProviderError(f"Index rebuild failed: {e}") from e

    def _rerank_candidates(
        self,
        session: Session,
        query: str,
        candidates: List[Dict]
    ) -> List[Dict]:
        """Rerank candidates using cross-encoder

        Args:
            session: Request-scoped SQLAlchemy session
            query: Original query text
            candidates: List of candidate dicts with entity_id, entity_type, score

        Returns:
            Reranked list of candidates (scores updated)
        """
        if not self.reranker_client:
            return candidates

        try:
            # Fetch entity content for reranking
            texts = []
            for candidate in candidates:
                entity = self._fetch_entity(session, candidate['entity_id'], candidate['entity_type'])
                if entity:
                    if candidate['entity_type'] == 'experience':
                        text = f"{entity.title}\n\n{entity.playbook}"
                    else:  # manual
                        text = entity.content or entity.title
                    texts.append(text)
                else:
                    texts.append("")  # Fallback

            # Rerank
            reranked_scores = self.reranker_client.rerank(query, texts)

            # Update scores
            for candidate, new_score in zip(candidates, reranked_scores):
                candidate['score'] = new_score

            # Sort by new scores
            candidates.sort(key=lambda x: x['score'], reverse=True)

            logger.debug(f"Reranked {len(candidates)} candidates")

            return candidates

        except Exception as e:
            logger.warning(f"Reranking failed, using FAISS scores: {e}")
            return candidates

    def _rerank_duplicates(
        self,
        session: Session,
        query_text: str,
        candidates: List[Dict]
    ) -> List[Dict]:
        """Rerank duplicate candidates"""
        if not self.reranker_client:
            return candidates

        try:
            # Use existing content from candidates
            texts = []
            for candidate in candidates:
                entity = self._fetch_entity(session, candidate['entity_id'], candidate['entity_type'])
                if entity:
                    if candidate['entity_type'] == 'experience':
                        text = f"{entity.title}\n\n{entity.playbook}"
                    else:
                        text = entity.content or entity.title
                    texts.append(text)
                else:
                    texts.append("")

            # Rerank
            reranked_scores = self.reranker_client.rerank(query_text, texts)

            # Update scores
            for candidate, new_score in zip(candidates, reranked_scores):
                candidate['score'] = new_score

            # Sort by new scores
            candidates.sort(key=lambda x: x['score'], reverse=True)

            return candidates

        except Exception as e:
            logger.warning(f"Reranking failed, using FAISS scores: {e}")
            return candidates

    def _fetch_entity(self, session: Session, entity_id: str, entity_type: str):
        """Fetch entity from database"""
        try:
            if entity_type == 'experience':
                return session.query(Experience).filter(
                    Experience.id == entity_id
                ).first()
            else:  # manual
                return session.query(CategoryManual).filter(
                    CategoryManual.id == entity_id
                ).first()
        except Exception as e:
            logger.warning(f"Failed to fetch {entity_type} {entity_id}: {e}")
            return None

    def _filter_by_category(
        self,
        session: Session,
        mappings: List[Dict],
        category_code: str
    ) -> List[Dict]:
        """Filter entity mappings by category"""
        filtered = []
        for mapping in mappings:
            entity = self._fetch_entity(session, mapping['entity_id'], mapping['entity_type'])
            if entity and entity.category_code == category_code:
                filtered.append(mapping)
        return filtered

    @property
    def name(self) -> str:
        return "vector_faiss"

    @property
    def is_available(self) -> bool:
        """Check if vector provider is available"""
        return self.index_manager.is_available
