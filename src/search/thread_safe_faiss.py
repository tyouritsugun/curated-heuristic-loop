"""Thread-safe wrapper for FAISSIndexManager with atomic persistence and recovery"""
import threading
import shutil
import logging
import time
from pathlib import Path
from typing import Optional, Tuple, List
import numpy as np
from sqlalchemy.orm import Session

from .faiss_index import FAISSIndexManager, FAISSIndexError

logger = logging.getLogger(__name__)


def initialize_faiss_with_recovery(
    config,
    session: Session,
    embedding_client=None
) -> Optional[FAISSIndexManager]:
    """Initialize FAISS index with automatic recovery.

    Recovery steps:
    1. Try to load existing index
    2. If load fails, try backup
    3. If backup fails, rebuild from database
    4. If rebuild fails, return None (disable FAISS, fall back to text search)

    Args:
        config: Configuration object with FAISS settings
        session: SQLAlchemy session for database access
        embedding_client: Optional EmbeddingClient for dimension detection (default: None, uses 768)

    Returns:
        FAISSIndexManager instance on success, None if FAISS should be disabled
    """
    from src.storage.schema import Embedding

    # Get dimension from embedding client if available, otherwise default to 768
    dimension = getattr(embedding_client, 'dimension', 768) if embedding_client else 768

    # Try to create FAISS manager
    try:
        faiss_manager = FAISSIndexManager(
            index_dir=str(config.faiss_index_path),
            model_name=config.embedding_repo,
            dimension=dimension,
            session=session
        )
    except Exception as e:
        logger.error(f"Failed to create FAISSIndexManager: {e}")
        return None

    # Try normal load
    try:
        _ = faiss_manager.index  # Triggers lazy load
        logger.info(
            f"FAISS index loaded successfully: {faiss_manager.index.ntotal} vectors"
        )
        return faiss_manager

    except FAISSIndexError as e:
        logger.warning(f"Failed to load FAISS index: {e}")

        # Try backup restore
        backup_path = faiss_manager.index_path.with_suffix('.index.backup')
        if backup_path.exists():
            try:
                logger.info("Attempting to restore from backup")
                shutil.copy2(backup_path, faiss_manager.index_path)

                # Reset internal state and reload
                faiss_manager._index = None
                _ = faiss_manager.index

                logger.info(
                    f"FAISS index restored from backup: {faiss_manager.index.ntotal} vectors"
                )
                return faiss_manager

            except Exception as e2:
                logger.error(f"Backup restore failed: {e2}")

        # Try rebuild from database
        try:
            logger.info("Attempting to rebuild index from database")

            # Create new empty index
            faiss_manager._create_new_index(reset_metadata=True)

            # Get all embeddings from database
            embeddings = session.query(Embedding).filter(
                Embedding.model_name == config.embedding_repo
            ).all()

            if not embeddings:
                logger.warning("No embeddings found in database, starting with empty index")
                # Save empty index
                faiss_manager.save()
                return faiss_manager

            # Group embeddings by entity
            from src.storage.schema import FAISSMetadata

            # Get entity type mapping
            entity_type_map = {}
            for emb in embeddings:
                # Infer entity type from ID prefix
                if emb.entity_id.startswith('EXP-'):
                    entity_type_map[emb.entity_id] = 'experience'
                elif emb.entity_id.startswith('MNL-'):
                    entity_type_map[emb.entity_id] = 'manual'
                else:
                    logger.warning(f"Unknown entity type for ID: {emb.entity_id}, skipping")
                    continue

            # Prepare bulk add
            entity_ids = []
            entity_types = []
            vectors = []

            for emb in embeddings:
                if emb.entity_id in entity_type_map:
                    entity_ids.append(emb.entity_id)
                    entity_types.append(entity_type_map[emb.entity_id])
                    vectors.append(emb.get_embedding())

            if entity_ids:
                # Stack vectors and add to index
                vectors_array = np.vstack(vectors).astype(np.float32)
                faiss_manager.add(entity_ids, entity_types, vectors_array)
                faiss_manager.save()

                logger.info(
                    f"FAISS index rebuilt successfully: {len(entity_ids)} vectors"
                )
            else:
                logger.warning("No valid entities found for rebuild")
                faiss_manager.save()

            return faiss_manager

        except Exception as e3:
            logger.error(f"Index rebuild failed: {e3}")
            logger.warning("FAISS will be unavailable, falling back to text search")
            return None


class PeriodicSaver:
    """Background thread that periodically saves FAISS index.

    Only used when save_policy = "periodic".
    """

    def __init__(self, faiss_manager: 'ThreadSafeFAISSManager', interval: int):
        """Initialize periodic saver.

        Args:
            faiss_manager: ThreadSafeFAISSManager instance to save
            interval: Save interval in seconds
        """
        self.faiss_manager = faiss_manager
        self.interval = interval
        self.dirty = False
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="FAISS-PeriodicSaver")

    def start(self):
        """Start periodic save thread."""
        self.thread.start()
        logger.info(f"Periodic FAISS saver started (interval={self.interval}s)")

    def stop(self):
        """Stop periodic save thread."""
        self.stop_event.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            logger.warning("Periodic saver thread did not stop cleanly")

    def mark_dirty(self):
        """Mark index as dirty (needs saving)."""
        self.dirty = True

    def _run(self):
        """Periodic save loop."""
        while not self.stop_event.wait(self.interval):
            if self.dirty:
                try:
                    self.faiss_manager._save_safely()
                    self.dirty = False
                    logger.debug("Periodic FAISS save completed")
                except Exception as e:
                    logger.error(f"Periodic FAISS save failed: {e}")


class ThreadSafeFAISSManager:
    """Thread-safe wrapper around FAISSIndexManager.

    Features:
    - RLock (reentrant lock) for thread-safe operations
    - Atomic save with backup and temp file
    - Automatic rebuild when tombstone ratio exceeds threshold
    - Configurable save policies (immediate/periodic/manual)

    Save Policies:
    - immediate: Save after every write operation (safest, slower)
    - periodic: Save every N seconds if dirty (balanced)
    - manual: Never auto-save, requires explicit save() call (fastest, risky)

    Rebuild Policy:
    - Automatically rebuilds when tombstone ratio > rebuild_threshold
    - Default threshold: 0.10 (10%)
    """

    def __init__(
        self,
        faiss_manager: FAISSIndexManager,
        save_policy: str = "immediate",
        save_interval: int = 300,
        rebuild_threshold: float = 0.10,
    ):
        """Initialize thread-safe FAISS manager.

        Args:
            faiss_manager: Underlying FAISSIndexManager instance
            save_policy: Save policy ("immediate", "periodic", or "manual")
            save_interval: Save interval in seconds (for periodic mode)
            rebuild_threshold: Tombstone ratio threshold for automatic rebuild (default: 0.10)
        """
        self._manager = faiss_manager
        self._lock = threading.RLock()  # Reentrant lock (update calls delete + add)
        self._save_policy = save_policy
        self._rebuild_threshold = rebuild_threshold
        self._periodic_saver: Optional[PeriodicSaver] = None

        # Validate save policy
        if save_policy not in ("immediate", "periodic", "manual"):
            raise ValueError(
                f"Invalid save_policy: {save_policy}. "
                f"Must be 'immediate', 'periodic', or 'manual'"
            )

        # Start periodic saver if needed
        if save_policy == "periodic":
            self._periodic_saver = PeriodicSaver(self, save_interval)
            self._periodic_saver.start()

        logger.info(
            f"ThreadSafeFAISSManager initialized: policy={save_policy}, "
            f"interval={save_interval}s, rebuild_threshold={rebuild_threshold}"
        )

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 10,
        entity_type: Optional[str] = None
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Thread-safe search.

        Args:
            query_embedding: Query vector
            top_k: Number of results
            entity_type: Optional entity type filter

        Returns:
            Tuple of (scores, internal_ids)
        """
        with self._lock:
            return self._manager.search(query_embedding, top_k, entity_type)

    def add(
        self,
        entity_ids: List[str],
        entity_types: List[str],
        embeddings: np.ndarray
    ) -> List[int]:
        """Thread-safe add with automatic save.

        Args:
            entity_ids: List of entity IDs
            entity_types: List of entity types
            embeddings: Embedding vectors

        Returns:
            List of FAISS internal IDs
        """
        with self._lock:
            result = self._manager.add(entity_ids, entity_types, embeddings)

            # Save based on policy
            if self._save_policy == "immediate":
                self._save_safely()
            elif self._save_policy == "periodic":
                self._periodic_saver.mark_dirty()

            return result

    def update(
        self,
        entity_id: str,
        entity_type: str,
        new_embedding: np.ndarray
    ) -> int:
        """Thread-safe update with automatic save.

        Args:
            entity_id: Entity ID to update
            entity_type: Entity type
            new_embedding: New embedding vector

        Returns:
            New FAISS internal ID
        """
        with self._lock:
            result = self._manager.update(entity_id, entity_type, new_embedding)

            # Save based on policy
            if self._save_policy == "immediate":
                self._save_safely()
            elif self._save_policy == "periodic":
                self._periodic_saver.mark_dirty()

            return result

    def delete(self, entity_id: str, entity_type: str) -> None:
        """Thread-safe delete with automatic save and rebuild check.

        Args:
            entity_id: Entity ID to delete
            entity_type: Entity type
        """
        with self._lock:
            result = self._manager.delete(entity_id, entity_type)

            # Save based on policy
            if self._save_policy == "immediate":
                self._save_safely()
            elif self._save_policy == "periodic":
                self._periodic_saver.mark_dirty()

            # Check if rebuild needed (tombstone ratio > threshold)
            tombstone_ratio = self._manager.get_tombstone_ratio()
            if tombstone_ratio > self._rebuild_threshold:
                logger.info(
                    f"Tombstone ratio {tombstone_ratio:.2%} exceeds threshold "
                    f"{self._rebuild_threshold:.2%}, triggering rebuild"
                )
                self._rebuild_index()

            return result

    def _save_safely(self):
        """Save index with atomic rename and backup.

        Strategy:
        1. Write to temp file
        2. Backup existing index (if exists)
        3. Atomic rename temp → main
        4. Save metadata

        Note: Assumes lock is already held by caller.
        """
        index_path = Path(self._manager.index_path)
        backup_path = index_path.with_suffix('.index.backup')
        temp_path = index_path.with_suffix('.index.tmp')

        try:
            # Write to temp file
            self._manager.faiss.write_index(self._manager.index, str(temp_path))

            # Backup existing index
            if index_path.exists():
                shutil.copy2(index_path, backup_path)

            # Atomic rename (on most filesystems)
            temp_path.rename(index_path)

            # Save metadata (metadata path is adjacent to index_path)
            # Use the existing save() method's metadata logic
            meta_path = Path(self._manager.meta_path)
            import json
            metadata = {
                "model_name": self._manager.model_name,
                "dimension": self._manager.dimension,
                "count": self._manager.index.ntotal,
                "checksum": self._manager._compute_checksum()
            }
            with open(meta_path, 'w') as f:
                json.dump(metadata, f, indent=2)

            logger.debug(
                f"FAISS index saved successfully: {index_path} "
                f"({self._manager.index.ntotal} vectors)"
            )

        except Exception as e:
            logger.error(f"Failed to save FAISS index: {e}")
            # Clean up temp file
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            raise FAISSIndexError(f"Failed to save index: {e}") from e

    def _rebuild_index(self):
        """Rebuild FAISS index from scratch.

        Process:
        1. Query all non-deleted embeddings from database
        2. Create new FAISS index
        3. Batch add all embeddings
        4. Clear tombstones in faiss_metadata
        5. Save new index atomically

        Note: Assumes lock is already held by caller.
        """
        from src.storage.schema import Embedding, FAISSMetadata

        logger.info("Starting FAISS index rebuild")
        session = self._manager.session

        if not session:
            logger.warning("Cannot rebuild index: no session available")
            return

        try:
            # Get all non-deleted metadata
            metadata_list = session.query(FAISSMetadata).filter(
                FAISSMetadata.deleted == False
            ).all()

            if not metadata_list:
                logger.warning("No non-deleted entries found for rebuild")
                # Create empty index
                self._manager._create_new_index(reset_metadata=True)
                self._save_safely()
                return

            # Build entity_id → entity_type mapping
            metadata_map = {
                meta.entity_id: meta.entity_type
                for meta in metadata_list
            }

            # Get embeddings for these entities
            embeddings = session.query(Embedding).filter(
                Embedding.model_name == self._manager.model_name,
                Embedding.entity_id.in_(list(metadata_map.keys()))
            ).all()

            if not embeddings:
                logger.warning("No embeddings found for non-deleted entries")
                self._manager._create_new_index(reset_metadata=True)
                self._save_safely()
                return

            # Prepare data for bulk add
            entity_ids = []
            entity_types = []
            embedding_vectors = []

            for emb in embeddings:
                if emb.entity_id in metadata_map:
                    entity_ids.append(emb.entity_id)
                    entity_types.append(metadata_map[emb.entity_id])
                    embedding_vectors.append(emb.get_embedding())

            if not entity_ids:
                logger.warning("No valid embeddings after filtering")
                self._manager._create_new_index(reset_metadata=True)
                self._save_safely()
                return

            # Stack embeddings
            embeddings_array = np.vstack(embedding_vectors).astype(np.float32)

            # Create new index and reset metadata
            self._manager._create_new_index(reset_metadata=True)

            # Batch add
            self._manager.add(entity_ids, entity_types, embeddings_array)

            # Save atomically
            self._save_safely()

            logger.info(
                f"FAISS index rebuild complete: {len(entity_ids)} vectors "
                f"(removed {len(metadata_list) - len(entity_ids)} tombstones)"
            )

        except Exception as e:
            logger.error(f"FAISS rebuild failed: {e}")
            try:
                session.rollback()
            except Exception:
                pass
            raise FAISSIndexError(f"Rebuild failed: {e}") from e

    def save(self) -> None:
        """Explicitly save index (for manual save policy)."""
        with self._lock:
            self._save_safely()

    def shutdown(self):
        """Shutdown periodic saver if running."""
        if self._periodic_saver:
            logger.info("Stopping periodic saver")
            self._periodic_saver.stop()

    @property
    def is_available(self) -> bool:
        """Check if FAISS is available."""
        return self._manager.is_available

    def get_entity_id(self, internal_id: int):
        """Get entity ID from FAISS internal ID."""
        with self._lock:
            return self._manager.get_entity_id(internal_id)

    def get_tombstone_ratio(self) -> float:
        """Get current tombstone ratio."""
        with self._lock:
            return self._manager.get_tombstone_ratio()

    def needs_rebuild(self) -> bool:
        """Check if index needs rebuilding."""
        with self._lock:
            return self._manager.needs_rebuild()

    def __getattr__(self, name):
        """Delegate other attributes to underlying manager (with lock)."""
        # For thread safety, wrap access in lock
        attr = getattr(self._manager, name)
        if callable(attr):
            # Wrap methods with lock
            def locked_method(*args, **kwargs):
                with self._lock:
                    return attr(*args, **kwargs)
            return locked_method
        return attr
