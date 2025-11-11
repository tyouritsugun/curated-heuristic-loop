"""FAISS index manager for unified vector search

Manages a single FAISS index containing both experiences and manuals.
Supports incremental updates (add, update, delete) and persistence.
"""
import os
import json
import logging
import hashlib
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from contextlib import contextmanager

try:
    import fcntl  # POSIX file locking
except Exception:  # pragma: no cover - platform without fcntl (e.g., Windows)
    fcntl = None  # type: ignore
import numpy as np

logger = logging.getLogger(__name__)


class FAISSIndexManager:
    """Manages FAISS index lifecycle with incremental updates

    Design:
    - Single unified index for experiences + manuals
    - Uses IndexFlatIP for cosine similarity (requires normalized embeddings)
    - Metadata mapping stored in SQLite faiss_metadata table
    - Supports add, update, delete operations for ~1k entries

    Thread-safety: Not thread-safe. Caller must synchronize access.
    """

    def __init__(
        self,
        index_dir: str,
        model_name: str,
        dimension: int,
        session=None,  # DEPRECATED: Use session_factory instead
        session_factory=None  # Callable that returns a new SQLAlchemy session
    ):
        """Initialize FAISS index manager

        Args:
            index_dir: Directory for index files (e.g., data/faiss_index)
            model_name: HuggingFace model identifier (e.g., Qwen/Qwen3-Embedding-0.6B)
            dimension: Embedding dimension (e.g., 768)
            session: DEPRECATED - Single session (for backward compatibility)
            session_factory: Callable that returns a new session (preferred)
        """
        self.index_dir = Path(index_dir)
        self.model_name = model_name
        self.dimension = dimension

        # Validate session availability and prefer session_factory
        if not session_factory and not session:
            logger.warning(
                "FAISSIndexManager initialized without session access. "
                "Database operations (metadata, rebuild) will fail. "
                "This is only acceptable for read-only index loading from files."
            )

        # Support both old (session) and new (session_factory) patterns
        if session_factory:
            self.session_factory = session_factory
            self.session = None  # Don't use shared session
            logger.debug("Using session_factory for database operations")
        else:
            # Backward compatibility: wrap single session in a factory
            self.session = session
            self.session_factory = None
            if session:
                logger.warning(
                    "Using deprecated single session pattern. "
                    "Consider migrating to session_factory for better concurrency."
                )

        # Create index directory if needed
        self.index_dir.mkdir(parents=True, exist_ok=True)

        # Generate index filename from model name
        # e.g., "Qwen/Qwen3-Embedding-0.6B" -> "unified_qwen3-embedding-0.6b"
        model_slug = model_name.lower().replace("/", "_").replace(".", "")
        self.index_filename = f"unified_{model_slug}.index"
        self.meta_filename = f"unified_{model_slug}.meta.json"

        self.index_path = self.index_dir / self.index_filename
        self.meta_path = self.index_dir / self.meta_filename

        # FAISS index (lazy loaded)
        self._index = None
        self._faiss = None  # faiss module (lazy import)

        # Lock file path for cross-process serialization of FAISS ops
        self._lock_path = self.index_dir / "faiss_index.lock"

    @property
    def index(self):
        """Get FAISS index (lazy load)"""
        if self._index is None:
            self._load_or_create_index()
        return self._index

    @property
    def faiss(self):
        """Get faiss module (lazy import)"""
        if self._faiss is None:
            try:
                import faiss
                self._faiss = faiss
            except ImportError as e:
                raise FAISSIndexError(
                    "faiss-cpu not installed. Install ML extras with: pip install -e \".[ml]\""
                ) from e
        return self._faiss

    def _load_or_create_index(self) -> None:
        """Load existing index or create new one"""
        if self.index_path.exists():
            try:
                logger.info(f"Loading FAISS index from {self.index_path}")
                self._index = self.faiss.read_index(str(self.index_path))

                # Validate dimension
                if self._index.d != self.dimension:
                    logger.warning(
                        f"Index dimension mismatch: expected {self.dimension}, "
                        f"got {self._index.d}. Rebuilding index."
                    )
                    self._create_new_index()
                    return

                logger.info(
                    f"FAISS index loaded: {self._index.ntotal} vectors, "
                    f"dimension={self._index.d}"
                )

                # Validate consistency with database
                self._validate_consistency()

            except Exception as e:
                logger.error(f"Failed to load FAISS index: {e}. Creating new index.")
                self._create_new_index()
        else:
            logger.info("No existing index found. Creating new index.")
            self._create_new_index()

    def _create_new_index(self, reset_metadata: bool = True) -> None:
        """Create new FAISS index

        Args:
            reset_metadata: When True, clear faiss_metadata to keep it in sync
                with the freshly created in-memory index.
        """
        logger.info(
            f"Creating new FAISS IndexFlatIP with dimension={self.dimension}"
        )

        if reset_metadata:
            try:
                with self._session_scope(read_only=False) as session:
                    from src.storage.schema import FAISSMetadata

                    deleted_rows = session.query(FAISSMetadata).delete()
                    if deleted_rows:
                        logger.info(
                            f"Cleared {deleted_rows} stale faiss_metadata rows "
                            "before creating fresh index"
                        )
                    # Commit handled by _session_scope

            except FAISSIndexError:
                logger.warning(
                    "No session available to reset FAISS metadata. "
                    "Proceeding with index creation (metadata may be inconsistent)."
                )
            except Exception as e:
                logger.warning(
                    f"Failed to reset FAISS metadata before rebuilding index: {e}"
                )
                raise

        # IndexFlatIP for cosine similarity (requires normalized embeddings)
        self._index = self.faiss.IndexFlatIP(self.dimension)

    def _validate_consistency(self) -> None:
        """Validate that index file matches database state.

        Logs warnings if inconsistencies detected (checksum mismatch, missing metadata).
        Called after index load to detect stale index files.
        """
        if not self.meta_path.exists():
            logger.warning(
                "Index exists but metadata file missing. "
                "This may indicate incomplete save. Rebuild recommended."
            )
            return

        try:
            with open(self.meta_path) as f:
                meta = json.load(f)

            saved_checksum = meta.get('checksum', '')
            saved_count = meta.get('count', 0)

            # Compute current checksum from database
            try:
                current_checksum = self._compute_checksum()
            except Exception as e:
                logger.warning(f"Cannot validate consistency: checksum computation failed: {e}")
                return

            if saved_checksum != current_checksum:
                logger.warning(
                    f"Index inconsistency detected! "
                    f"Metadata checksum={saved_checksum}, "
                    f"Database checksum={current_checksum}. "
                    f"Index file may be stale. Rebuild recommended to resync."
                )
                # Note: We don't auto-rebuild here to avoid startup delays
                # The needs_rebuild() method will detect this and trigger rebuild when appropriate

            # Also check if vector count makes sense
            if self._index.ntotal != saved_count:
                logger.warning(
                    f"Index vector count mismatch: "
                    f"FAISS has {self._index.ntotal} vectors, "
                    f"metadata says {saved_count}. "
                    f"Metadata file may be corrupted."
                )

        except json.JSONDecodeError as e:
            logger.warning(f"Metadata file corrupted (invalid JSON): {e}")
        except Exception as e:
            logger.warning(f"Consistency validation failed: {e}")

    @contextmanager
    def _exclusive_lock(self):
        """Cross-process exclusive lock around critical FAISS operations.

        Uses fcntl.flock on a lock file within the index directory. On
        non-POSIX platforms where fcntl is unavailable, acts as a no-op
        to preserve functionality (but concurrency guarantees are reduced).
        """
        if fcntl is None:
            # No-op lock on unsupported platforms
            yield
            return
        # Ensure directory exists
        self.index_dir.mkdir(parents=True, exist_ok=True)
        # Open lock file for the duration of the critical section
        with open(self._lock_path, "a+") as fp:
            try:
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
            except Exception:
                # If we cannot lock, proceed without to avoid deadlock
                yield
                return
            try:
                yield
            finally:
                try:
                    fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass

    @contextmanager
    def _session_scope(self, read_only: bool = False):
        """Context manager for session lifecycle with proper cleanup.

        Args:
            read_only: If True, hint that this is a read-only operation
                      (no automatic commit/flush even if we own the session)

        Yields:
            SQLAlchemy session

        Raises:
            FAISSIndexError: If no session available
        """
        if self.session_factory:
            session = self.session_factory()
            owns_session = True
        elif self.session:
            session = self.session
            owns_session = False
        else:
            raise FAISSIndexError(
                "No database session available. "
                "FAISSIndexManager requires session_factory for database operations."
            )

        try:
            yield session

            # Success path: commit or flush depending on ownership
            if owns_session and not read_only:
                # We own the session, commit changes
                session.commit()
            elif not owns_session and not read_only:
                # Shared session (CLI/script), flush to make changes visible
                # but don't commit (caller owns the transaction)
                session.flush()

        except Exception as e:
            # ALWAYS rollback on error, even for shared sessions!
            # Shared sessions left in "pending rollback" state are broken
            try:
                session.rollback()
            except Exception as rollback_error:
                logger.warning(f"Failed to rollback session: {rollback_error}")
            raise
        finally:
            # Only close if we own the session
            if owns_session:
                try:
                    session.close()
                except Exception as close_error:
                    logger.warning(f"Failed to close session: {close_error}")

    def add(
        self,
        entity_ids: List[str],
        entity_types: List[str],
        embeddings: np.ndarray
    ) -> List[int]:
        """Add vectors to index

        Args:
            entity_ids: List of entity IDs (experience or manual IDs)
            entity_types: List of entity types ('experience' or 'manual')
            embeddings: Numpy array of shape (n, dimension), dtype=float32

        Returns:
            List of FAISS internal IDs assigned to the vectors

        Raises:
            FAISSIndexError: If add operation fails
        """
        if len(entity_ids) != len(entity_types) or len(entity_ids) != embeddings.shape[0]:
            raise ValueError(
                f"Mismatched lengths: entity_ids={len(entity_ids)}, "
                f"entity_types={len(entity_types)}, embeddings={embeddings.shape[0]}"
            )

        if embeddings.shape[1] != self.dimension:
            raise ValueError(
                f"Embedding dimension mismatch: expected {self.dimension}, "
                f"got {embeddings.shape[1]}"
            )

        try:
            with self._exclusive_lock():
                # Get starting internal ID (next sequential ID)
                start_id = self.index.ntotal

                # Add vectors to FAISS
                self.index.add(embeddings)

                # Generate internal IDs
                internal_ids = list(range(start_id, start_id + len(entity_ids)))

                # Store metadata mappings (while lock is held)
                self._save_metadata_mappings(entity_ids, entity_types, internal_ids)

                logger.info(
                    f"Added {len(entity_ids)} vectors to FAISS index "
                    f"(total: {self.index.ntotal})"
                )

                return internal_ids

        except Exception as e:
            if self.session:
                try:
                    self.session.rollback()
                except Exception as rollback_error:
                    logger.warning(
                        f"Failed to rollback session after FAISS add error: {rollback_error}"
                    )
            raise FAISSIndexError(f"Failed to add vectors to index: {e}") from e

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 10,
        entity_type: Optional[str] = None
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Search index for similar vectors

        Args:
            query_embedding: Query vector of shape (dimension,) or (1, dimension)
            top_k: Number of results to return
            entity_type: Filter by 'experience' or 'manual' (None for both)

        Returns:
            Tuple of (scores, internal_ids)
            - scores: numpy array of shape (top_k,) with cosine similarities
            - internal_ids: numpy array of shape (top_k,) with FAISS internal IDs

        Raises:
            FAISSIndexError: If search fails
        """
        try:
            # Ensure query is 2D
            if query_embedding.ndim == 1:
                query_embedding = query_embedding.reshape(1, -1)

            if query_embedding.shape[1] != self.dimension:
                raise ValueError(
                    f"Query embedding dimension mismatch: expected {self.dimension}, "
                    f"got {query_embedding.shape[1]}"
                )

            # Retrieve more candidates if filtering by entity_type
            # (will filter post-search)
            retrieve_k = top_k * 3 if entity_type else top_k
            retrieve_k = min(retrieve_k, self.index.ntotal)

            if retrieve_k == 0:
                # Empty index
                return np.array([]), np.array([])

            # Search FAISS
            scores, internal_ids = self.index.search(query_embedding, retrieve_k)

            # Flatten results (query_embedding is shape (1, dimension))
            scores = scores[0]
            internal_ids = internal_ids[0]

            # Filter by entity_type if specified
            if entity_type:
                filtered_scores = []
                filtered_ids = []

                for score, internal_id in zip(scores, internal_ids):
                    if internal_id == -1:  # FAISS returns -1 for no result
                        continue

                    # Lookup entity_type from metadata
                    meta = self._get_metadata_by_internal_id(int(internal_id))
                    if meta and meta['entity_type'] == entity_type:
                        filtered_scores.append(score)
                        filtered_ids.append(internal_id)

                        if len(filtered_ids) >= top_k:
                            break

                scores = np.array(filtered_scores[:top_k])
                internal_ids = np.array(filtered_ids[:top_k])
            else:
                # Filter out -1 (no result) entries
                valid_mask = internal_ids != -1
                scores = scores[valid_mask][:top_k]
                internal_ids = internal_ids[valid_mask][:top_k]

            logger.debug(
                f"FAISS search found {len(internal_ids)} results "
                f"(top_k={top_k}, entity_type={entity_type})"
            )

            return scores, internal_ids

        except Exception as e:
            raise FAISSIndexError(f"FAISS search failed: {e}") from e

    def delete(self, entity_id: str, entity_type: str) -> None:
        """Delete vector from index

        Marks entries as tombstones in metadata (lazy deletion).
        Actual removal deferred to rebuild when tombstone ratio > 10%

        Args:
            entity_id: Entity ID to delete
            entity_type: Entity type ('experience' or 'manual')

        Raises:
            FAISSIndexError: If delete fails
        """
        try:
            with self._exclusive_lock():
                # Mark as deleted in metadata
                self._mark_deleted(entity_id, entity_type)

                logger.info(f"Marked {entity_type} {entity_id} as deleted in metadata")

        except Exception as e:
            raise FAISSIndexError(f"Failed to delete from index: {e}") from e

    def update(
        self,
        entity_id: str,
        entity_type: str,
        new_embedding: np.ndarray
    ) -> int:
        """Update vector in index

        Strategy: Delete old + Add new

        Args:
            entity_id: Entity ID to update
            entity_type: Entity type
            new_embedding: New embedding vector

        Returns:
            New FAISS internal ID

        Raises:
            FAISSIndexError: If update fails
        """
        try:
            with self._exclusive_lock():
                # Delete old entry
                self.delete(entity_id, entity_type)

                # Add new entry
                new_embedding_2d = new_embedding.reshape(1, -1)
                new_ids = self.add([entity_id], [entity_type], new_embedding_2d)

                logger.info(f"Updated {entity_type} {entity_id} in FAISS index")

                return new_ids[0]

        except Exception as e:
            raise FAISSIndexError(f"Failed to update vector: {e}") from e

    def save(self) -> None:
        """Persist index to disk"""
        try:
            with self._exclusive_lock():
                # Write FAISS index
                self.faiss.write_index(self.index, str(self.index_path))

                # Write metadata
                metadata = {
                    "model_name": self.model_name,
                    "dimension": self.dimension,
                    "count": self.index.ntotal,
                    "checksum": self._compute_checksum()
                }

                with open(self.meta_path, 'w') as f:
                    json.dump(metadata, f, indent=2)

                logger.info(
                    f"FAISS index saved: {self.index_path} "
                    f"({self.index.ntotal} vectors)"
                )

        except Exception as e:
            raise FAISSIndexError(f"Failed to save index: {e}") from e

    def get_entity_id(self, internal_id: int) -> Optional[Dict[str, str]]:
        """Get entity ID and type from FAISS internal ID

        Args:
            internal_id: FAISS internal ID

        Returns:
            Dict with 'entity_id' and 'entity_type', or None if not found
        """
        return self._get_metadata_by_internal_id(internal_id)

    def get_tombstone_ratio(self) -> float:
        """Get ratio of deleted entries to total entries

        Returns:
            Ratio between 0.0 and 1.0
        """
        try:
            with self._session_scope(read_only=True) as session:
                from src.storage.schema import FAISSMetadata

                total = session.query(FAISSMetadata).count()
                if total == 0:
                    return 0.0

                deleted = session.query(FAISSMetadata).filter(
                    FAISSMetadata.deleted == True
                ).count()

                return deleted / total

        except FAISSIndexError:
            logger.warning("No session available for tombstone ratio computation")
            return 0.0
        except Exception as e:
            logger.warning(f"Failed to compute tombstone ratio: {e}")
            return 0.0

    def needs_rebuild(self) -> bool:
        """Check if index needs rebuilding

        Rebuild triggers:
        - Tombstone ratio > 10%
        - Metadata checksum mismatch
        - Dimension mismatch

        Returns:
            True if rebuild needed
        """
        # Check tombstone ratio
        if self.get_tombstone_ratio() > 0.10:
            logger.info("Rebuild needed: tombstone ratio > 10%")
            return True

        # Check metadata checksum
        if self.meta_path.exists():
            try:
                with open(self.meta_path) as f:
                    meta = json.load(f)

                current_checksum = self._compute_checksum()
                if meta.get('checksum') != current_checksum:
                    logger.info("Rebuild needed: checksum mismatch")
                    return True

            except Exception as e:
                logger.warning(f"Failed to validate metadata: {e}")
                return True

        return False

    def _save_metadata_mappings(
        self,
        entity_ids: List[str],
        entity_types: List[str],
        internal_ids: List[int]
    ) -> None:
        """Save metadata mappings to database with retry logic

        Uses separate session (from session_factory) to avoid blocking
        the main transaction. This allows the caller to commit their
        transaction before this method completes.
        """
        # If using session_factory, create a new session for this operation
        if self.session_factory:
            session = self.session_factory()
            owns_session = True
        elif self.session:
            # Backward compatibility: use shared session
            session = self.session
            owns_session = False
        else:
            # No session available, skip metadata save
            logger.warning("No session available for FAISS metadata save")
            return

        import time
        from sqlalchemy.exc import OperationalError

        max_retries = 3
        base_delay = 0.1  # 100ms

        try:
            for attempt in range(max_retries):
                try:
                    from src.storage.schema import FAISSMetadata, utc_now

                    for entity_id, entity_type, internal_id in zip(
                        entity_ids, entity_types, internal_ids
                    ):
                        # Check if mapping already exists
                        existing = session.query(FAISSMetadata).filter(
                            FAISSMetadata.entity_id == entity_id,
                            FAISSMetadata.entity_type == entity_type
                        ).first()

                        if existing:
                            # Update existing mapping
                            existing.faiss_internal_id = internal_id
                            existing.deleted = False
                        else:
                            # Create new mapping
                            mapping = FAISSMetadata(
                                entity_id=entity_id,
                                entity_type=entity_type,
                                faiss_internal_id=internal_id,
                                created_at=utc_now(),
                                deleted=False
                            )
                            session.add(mapping)

                    # Commit if we own the session, otherwise just flush
                    if owns_session:
                        session.commit()
                    else:
                        session.flush()
                    return  # Success - exit retry loop

                except OperationalError as e:
                    # Check if it's a database lock error
                    if "database is locked" in str(e):
                        if attempt < max_retries - 1:
                            # Exponential backoff: 100ms, 200ms, 400ms
                            delay = base_delay * (2 ** attempt)
                            logger.debug(
                                f"Database locked when saving FAISS metadata, "
                                f"retrying in {delay:.3f}s (attempt {attempt + 1}/{max_retries})"
                            )
                            try:
                                session.rollback()
                            except Exception:
                                pass
                            time.sleep(delay)
                            continue
                        else:
                            # Final attempt failed
                            logger.error(f"Failed to save metadata mappings after {max_retries} retries: {e}")
                            try:
                                session.rollback()
                            except Exception as rollback_error:
                                logger.warning(
                                    f"Failed to rollback session after metadata save error: {rollback_error}"
                                )
                            raise
                    else:
                        # Different operational error - don't retry
                        raise

                except Exception as e:
                    # Non-operational error - don't retry
                    try:
                        session.rollback()
                    except Exception as rollback_error:
                        logger.warning(
                            f"Failed to rollback session after metadata save error: {rollback_error}"
                        )
                    logger.error(f"Failed to save metadata mappings: {e}")
                    raise

        finally:
            # Close session if we created it
            if owns_session:
                try:
                    session.close()
                except Exception as e:
                    logger.warning(f"Error closing FAISS metadata session: {e}")

    def _get_metadata_by_internal_id(self, internal_id: int) -> Optional[Dict[str, str]]:
        """Get entity metadata by FAISS internal ID"""
        try:
            with self._session_scope(read_only=True) as session:
                from src.storage.schema import FAISSMetadata

                mapping = session.query(FAISSMetadata).filter(
                    FAISSMetadata.faiss_internal_id == internal_id,
                    FAISSMetadata.deleted == False
                ).first()

                if mapping:
                    return {
                        'entity_id': mapping.entity_id,
                        'entity_type': mapping.entity_type
                    }

                return None

        except FAISSIndexError:
            logger.warning("No session available for metadata lookup")
            return None
        except Exception as e:
            logger.warning(f"Failed to lookup metadata for internal_id={internal_id}: {e}")
            return None

    def _mark_deleted(self, entity_id: str, entity_type: str) -> None:
        """Mark entry as deleted in metadata"""
        try:
            with self._session_scope(read_only=False) as session:
                from src.storage.schema import FAISSMetadata

                mapping = session.query(FAISSMetadata).filter(
                    FAISSMetadata.entity_id == entity_id,
                    FAISSMetadata.entity_type == entity_type
                ).first()

                if mapping:
                    mapping.deleted = True
                    # Commit/flush handled by _session_scope

        except FAISSIndexError:
            logger.warning("No session available to mark entry as deleted")
            return
        except Exception as e:
            logger.error(f"Failed to mark as deleted: {e}")
            raise

    def _compute_checksum(self) -> str:
        """Compute checksum of embeddings table for validation"""
        try:
            with self._session_scope(read_only=True) as session:
                from src.storage.schema import Embedding

                # Count embeddings for this model
                count = session.query(Embedding).filter(
                    Embedding.model_name == self.model_name
                ).count()

                # Simple checksum based on count (can be enhanced)
                return hashlib.md5(f"{self.model_name}:{count}".encode()).hexdigest()

        except FAISSIndexError:
            logger.warning("No session available for checksum computation")
            return ""
        except Exception as e:
            logger.warning(f"Failed to compute checksum: {e}")
            return ""

    @property
    def is_available(self) -> bool:
        """Check if FAISS is available"""
        try:
            _ = self.faiss
            return True
        except FAISSIndexError:
            return False


class FAISSIndexError(Exception):
    """Exception raised by FAISSIndexManager"""
    pass
