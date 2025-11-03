"""Embedding generation service for experiences and manuals

Handles async embedding generation workflow:
1. Entity created with embedding_status='pending'
2. Service generates embedding
3. Stores in embeddings table
4. Updates entity status to 'embedded'
5. Updates FAISS index incrementally
"""
import logging
from typing import List, Dict, Optional
import numpy as np

from sqlalchemy import event
from sqlalchemy.orm import Session

from ..storage.schema import Experience, CategoryManual
from ..storage.repository import EmbeddingRepository, ExperienceRepository, CategoryManualRepository
from .client import EmbeddingClient, EmbeddingClientError

logger = logging.getLogger(__name__)

_FAISS_OPS_KEY = "_chl_faiss_ops"


def _faiss_after_commit(session):
    """Execute deferred FAISS operations once the surrounding transaction commits.

    Implements retry logic with exponential backoff for failed operations.
    """
    import time

    info = session.info.get(_FAISS_OPS_KEY)
    if not info:
        return

    ops = info.get("ops", [])
    if not ops:
        return

    managers_to_save = set()
    failed_ops = []

    # Process each operation with retries
    for manager, description, operation in ops:
        max_retries = 3
        retry_delay = 0.1  # Start with 100ms
        success = False

        for attempt in range(max_retries):
            try:
                operation()
                managers_to_save.add(manager)
                success = True
                break
            except Exception as exc:
                if attempt < max_retries - 1:
                    logger.warning(
                        "Deferred FAISS operation failed (%s), attempt %d/%d: %s. Retrying in %.2fs...",
                        description, attempt + 1, max_retries, exc, retry_delay
                    )
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logger.error(
                        "Deferred FAISS operation failed permanently (%s) after %d attempts: %s",
                        description, max_retries, exc, exc_info=True
                    )
                    failed_ops.append((manager, description, operation, exc))

    # Persist indices that had successful operations
    for manager in managers_to_save:
        try:
            manager.save()
            logger.info("Successfully persisted FAISS index '%s'", manager.model_name)
        except Exception as exc:
            logger.error("Failed to persist FAISS index '%s': %s", manager.model_name, exc, exc_info=True)

    # Store failed operations for potential manual retry
    if failed_ops:
        if "failed_ops" not in info:
            info["failed_ops"] = []
        info["failed_ops"].extend([(desc, str(exc)) for _, desc, _, exc in failed_ops])
        logger.error(
            "WARNING: %d FAISS operation(s) failed permanently. "
            "These entities may not be searchable. Consider running a full index rebuild.",
            len(failed_ops)
        )

    ops.clear()


def _faiss_after_rollback(session):
    """Discard deferred FAISS operations when the transaction rolls back."""
    info = session.info.get(_FAISS_OPS_KEY)
    if info:
        info.get("ops", []).clear()


class EmbeddingService:
    """Service for generating and managing embeddings

    Coordinates between:
    - EmbeddingClient: Generate vectors
    - EmbeddingRepository: Store in database
    - Entity repositories: Update status
    - FAISSIndexManager: Update search index

    Thread-safety: Not thread-safe. Caller must synchronize access.
    """

    def __init__(
        self,
        session: Session,
        embedding_client: EmbeddingClient,
        faiss_index_manager: Optional['FAISSIndexManager'] = None,
        max_tokens: int = 8000,
    ):
        """Initialize embedding service

        Args:
            session: SQLAlchemy session
            embedding_client: Client for generating embeddings
            faiss_index_manager: Optional FAISS manager for index updates
            max_tokens: Max tokens for manual content (default: 8000)
        """
        self.session = session
        self.embedding_client = embedding_client
        self.faiss_index_manager = faiss_index_manager
        self.max_tokens = max_tokens

        # Initialize repositories
        self.emb_repo = EmbeddingRepository(session)
        self.exp_repo = ExperienceRepository(session)
        self.manual_repo = CategoryManualRepository(session)

        if self.faiss_index_manager:
            self._ensure_faiss_hooks()

    def generate_for_experience(self, experience_id: str) -> bool:
        """Generate embedding for an experience

        Args:
            experience_id: Experience ID

        Returns:
            True if successful, False otherwise
        """
        try:
            # Fetch experience
            exp = self.exp_repo.get_by_id(experience_id)
            if not exp:
                logger.error(f"Experience not found: {experience_id}")
                return False

            # Generate embedding content (title + playbook)
            content = f"{exp.title}\n\n{exp.playbook}"

            # Generate embedding
            try:
                embedding = self.embedding_client.encode_single(content)
            except EmbeddingClientError as e:
                logger.error(f"Failed to generate embedding for {experience_id}: {e}")
                exp.embedding_status = 'failed'
                self.session.flush()
                return False

            # Store in embeddings table
            self.emb_repo.create(
                entity_id=experience_id,
                entity_type='experience',
                model_name=getattr(self.embedding_client, "model_repo", ""),
                model_version=self.embedding_client.get_model_version(),
                embedding=embedding
            )

            # Update experience status
            exp.embedding_status = 'embedded'
            self.session.flush()

            # Update FAISS index if available (after commit)
            if self.faiss_index_manager:
                self._queue_faiss_add(
                    entity_id=experience_id,
                    entity_type='experience',
                    embedding_vector=embedding,
                    description=f"add experience {experience_id}",
                )

            logger.info(f"Generated embedding for experience: {experience_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to generate embedding for experience {experience_id}: {e}")
            # Try to mark as failed
            try:
                exp = self.exp_repo.get_by_id(experience_id)
                if exp:
                    exp.embedding_status = 'failed'
                    self.session.flush()
            except Exception:
                pass
            return False

    def generate_for_manual(self, manual_id: str) -> bool:
        """Generate embedding for a manual

        Args:
            manual_id: Manual ID

        Returns:
            True if successful, False otherwise
        """
        try:
            # Fetch manual
            manual = self.manual_repo.get_by_id(manual_id)
            if not manual:
                logger.error(f"Manual not found: {manual_id}")
                return False

            # Get content (truncate if needed)
            content = manual.content or manual.title

            # TODO: Token counting and truncation at 8000 tokens
            # For now, use simple character limit (rough approximation: 1 token ≈ 4 chars)
            max_chars = self.max_tokens * 4
            if len(content) > max_chars:
                logger.warning(
                    f"Manual {manual_id} exceeds {self.max_tokens} tokens "
                    f"({len(content)} chars). Truncating."
                )
                content = content[:max_chars]

            # Generate embedding
            try:
                embedding = self.embedding_client.encode_single(content)
            except EmbeddingClientError as e:
                logger.error(f"Failed to generate embedding for {manual_id}: {e}")
                manual.embedding_status = 'failed'
                self.session.flush()
                return False

            # Store in embeddings table
            self.emb_repo.create(
                entity_id=manual_id,
                entity_type='manual',
                model_name=getattr(self.embedding_client, "model_repo", ""),
                model_version=self.embedding_client.get_model_version(),
                embedding=embedding
            )

            # Update manual status
            manual.embedding_status = 'embedded'
            self.session.flush()

            # Update FAISS index if available (after commit)
            if self.faiss_index_manager:
                self._queue_faiss_add(
                    entity_id=manual_id,
                    entity_type='manual',
                    embedding_vector=embedding,
                    description=f"add manual {manual_id}",
                )

            logger.info(f"Generated embedding for manual: {manual_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to generate embedding for manual {manual_id}: {e}")
            # Try to mark as failed
            try:
                manual = self.manual_repo.get_by_id(manual_id)
                if manual:
                    manual.embedding_status = 'failed'
                    self.session.flush()
            except Exception:
                pass
            return False

    def upsert_for_experience(self, experience_id: str) -> bool:
        """Generate embedding and add/update FAISS vector for an experience

        Uses add() for new embeddings and update() for existing ones.
        """
        try:
            # Fetch experience
            exp = self.exp_repo.get_by_id(experience_id)
            if not exp:
                logger.error(f"Experience not found: {experience_id}")
                return False

            content = f"{exp.title}\n\n{exp.playbook}"
            try:
                embedding = self.embedding_client.encode_single(content)
            except EmbeddingClientError as e:
                logger.error(f"Failed to generate embedding for {experience_id}: {e}")
                exp.embedding_status = 'failed'
                self.session.flush()
                return False

            # Check existing embedding row
            existing = self.emb_repo.get_by_entity(
                entity_id=experience_id,
                entity_type='experience',
                model_name=getattr(self.embedding_client, "model_repo", ""),
            )

            # Upsert embedding row
            self.emb_repo.create(
                entity_id=experience_id,
                entity_type='experience',
                model_name=getattr(self.embedding_client, "model_repo", ""),
                model_version=self.embedding_client.get_model_version(),
                embedding=embedding,
            )

            # Update status
            exp.embedding_status = 'embedded'
            self.session.flush()

            # Update FAISS index if available (after commit)
            if self.faiss_index_manager:
                if existing is not None:
                    self._queue_faiss_update(
                        entity_id=experience_id,
                        entity_type='experience',
                        embedding_vector=embedding,
                        description=f"update experience {experience_id}",
                    )
                else:
                    self._queue_faiss_add(
                        entity_id=experience_id,
                        entity_type='experience',
                        embedding_vector=embedding,
                        description=f"add experience {experience_id}",
                    )

            logger.info(f"Upserted embedding for experience: {experience_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to upsert embedding for experience {experience_id}: {e}")
            try:
                exp = self.exp_repo.get_by_id(experience_id)
                if exp:
                    exp.embedding_status = 'failed'
                    self.session.flush()
            except Exception:
                pass
            return False

    def upsert_for_manual(self, manual_id: str) -> bool:
        """Generate embedding and add/update FAISS vector for a manual"""
        try:
            manual = self.manual_repo.get_by_id(manual_id)
            if not manual:
                logger.error(f"Manual not found: {manual_id}")
                return False

            content = manual.content or manual.title
            max_chars = self.max_tokens * 4
            if len(content) > max_chars:
                content = content[:max_chars]

            try:
                embedding = self.embedding_client.encode_single(content)
            except EmbeddingClientError as e:
                logger.error(f"Failed to generate embedding for {manual_id}: {e}")
                manual.embedding_status = 'failed'
                self.session.flush()
                return False

            existing = self.emb_repo.get_by_entity(
                entity_id=manual_id,
                entity_type='manual',
                model_name=getattr(self.embedding_client, "model_repo", ""),
            )

            self.emb_repo.create(
                entity_id=manual_id,
                entity_type='manual',
                model_name=getattr(self.embedding_client, "model_repo", ""),
                model_version=self.embedding_client.get_model_version(),
                embedding=embedding,
            )

            manual.embedding_status = 'embedded'
            self.session.flush()

            if self.faiss_index_manager:
                if existing is not None:
                    self._queue_faiss_update(
                        entity_id=manual_id,
                        entity_type='manual',
                        embedding_vector=embedding,
                        description=f"update manual {manual_id}",
                    )
                else:
                    self._queue_faiss_add(
                        entity_id=manual_id,
                        entity_type='manual',
                        embedding_vector=embedding,
                        description=f"add manual {manual_id}",
                    )

            logger.info(f"Generated embedding for manual: {manual_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to upsert embedding for manual {manual_id}: {e}")
            # Try to mark as failed
            try:
                manual = self.manual_repo.get_by_id(manual_id)
                if manual:
                    manual.embedding_status = 'failed'
                    self.session.flush()
            except Exception:
                pass
            return False

    def generate_for_entities(
        self,
        entity_ids: List[str],
        entity_type: str
    ) -> Dict[str, bool]:
        """Generate embeddings for multiple entities

        Args:
            entity_ids: List of entity IDs
            entity_type: 'experience' or 'manual'

        Returns:
            Dict mapping entity_id to success status
        """
        results = {}

        for entity_id in entity_ids:
            if entity_type == 'experience':
                success = self.generate_for_experience(entity_id)
            elif entity_type == 'manual':
                success = self.generate_for_manual(entity_id)
            else:
                logger.error(f"Invalid entity_type: {entity_type}")
                success = False

            results[entity_id] = success

        return results

    def get_pending_experiences(self) -> List[Experience]:
        """Get experiences with pending embeddings"""
        return self.session.query(Experience).filter(
            Experience.embedding_status == 'pending'
        ).all()

    def get_pending_manuals(self) -> List[CategoryManual]:
        """Get manuals with pending embeddings"""
        return self.session.query(CategoryManual).filter(
            CategoryManual.embedding_status == 'pending'
        ).all()

    def get_failed_experiences(self) -> List[Experience]:
        """Get experiences with failed embeddings"""
        return self.session.query(Experience).filter(
            Experience.embedding_status == 'failed'
        ).all()

    def get_failed_manuals(self) -> List[CategoryManual]:
        """Get manuals with failed embeddings"""
        return self.session.query(CategoryManual).filter(
            CategoryManual.embedding_status == 'failed'
        ).all()

    def process_pending(self, max_count: Optional[int] = None) -> Dict[str, int]:
        """Process pending embeddings for both experiences and manuals

        Args:
            max_count: Maximum number of entities to process (None for all)

        Returns:
            Dict with counts: {'processed': N, 'succeeded': M, 'failed': K}
        """
        stats = {'processed': 0, 'succeeded': 0, 'failed': 0}

        # Get pending entities
        pending_exp = self.get_pending_experiences()
        pending_man = self.get_pending_manuals()

        # Combine and limit
        all_pending = [
            ('experience', exp.id) for exp in pending_exp
        ] + [
            ('manual', man.id) for man in pending_man
        ]

        if max_count:
            all_pending = all_pending[:max_count]

        # Process each entity
        for entity_type, entity_id in all_pending:
            if entity_type == 'experience':
                success = self.generate_for_experience(entity_id)
            else:
                success = self.generate_for_manual(entity_id)

            stats['processed'] += 1
            if success:
                stats['succeeded'] += 1
            else:
                stats['failed'] += 1

        logger.info(
            f"Processed {stats['processed']} pending embeddings: "
            f"{stats['succeeded']} succeeded, {stats['failed']} failed"
        )

        return stats

    def retry_failed(self, max_count: Optional[int] = None) -> Dict[str, int]:
        """Retry failed embeddings

        Args:
            max_count: Maximum number of entities to retry (None for all)

        Returns:
            Dict with counts: {'retried': N, 'succeeded': M, 'failed': K}
        """
        stats = {'retried': 0, 'succeeded': 0, 'failed': 0}

        # Get failed entities
        failed_exp = self.get_failed_experiences()
        failed_man = self.get_failed_manuals()

        # Combine and limit
        all_failed = [
            ('experience', exp.id) for exp in failed_exp
        ] + [
            ('manual', man.id) for man in failed_man
        ]

        if max_count:
            all_failed = all_failed[:max_count]

        # Retry each entity
        for entity_type, entity_id in all_failed:
            # Reset status to pending before retry
            if entity_type == 'experience':
                exp = self.exp_repo.get_by_id(entity_id)
                if exp:
                    exp.embedding_status = 'pending'
                    self.session.flush()
                success = self.generate_for_experience(entity_id)
            else:
                manual = self.manual_repo.get_by_id(entity_id)
                if manual:
                    manual.embedding_status = 'pending'
                    self.session.flush()
                success = self.generate_for_manual(entity_id)

            stats['retried'] += 1
            if success:
                stats['succeeded'] += 1
            else:
                stats['failed'] += 1

        logger.info(
            f"Retried {stats['retried']} failed embeddings: "
            f"{stats['succeeded']} succeeded, {stats['failed']} still failed"
        )

        return stats

    def _ensure_faiss_hooks(self) -> None:
        """Ensure deferred FAISS operations are tied to the session lifecycle."""
        info = self.session.info.setdefault(_FAISS_OPS_KEY, {"ops": [], "listeners": False})
        if not info.get("listeners"):
            event.listen(self.session, "after_commit", _faiss_after_commit)
            event.listen(self.session, "after_rollback", _faiss_after_rollback)
            info["listeners"] = True

    def _queue_faiss_add(self, *, entity_id: str, entity_type: str, embedding_vector: np.ndarray, description: str) -> None:
        """Queue an add operation for execution after a successful commit."""
        self._queue_faiss_op(
            description,
            lambda: self.faiss_index_manager.add(
                entity_ids=[entity_id],
                entity_types=[entity_type],
                embeddings=embedding_vector.astype(np.float32, copy=True).reshape(1, -1),
            ),
        )

    def _queue_faiss_update(self, *, entity_id: str, entity_type: str, embedding_vector: np.ndarray, description: str) -> None:
        """Queue an update operation for execution after a successful commit."""
        self._queue_faiss_op(
            description,
            lambda: self.faiss_index_manager.update(
                entity_id=entity_id,
                entity_type=entity_type,
                new_embedding=embedding_vector.astype(np.float32, copy=True),
            ),
        )

    def _queue_faiss_op(self, description: str, operation) -> None:
        """Register a deferred FAISS operation that runs only if the transaction commits."""
        if not self.faiss_index_manager:
            return
        info = self.session.info.setdefault(_FAISS_OPS_KEY, {"ops": [], "listeners": False})
        if not info.get("listeners"):
            self._ensure_faiss_hooks()
        info["ops"].append((self.faiss_index_manager, description, operation))
