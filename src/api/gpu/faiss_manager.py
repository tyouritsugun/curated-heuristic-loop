"""GPU FAISS index management module.

Provides FAISSIndexManager, FAISSIndexError, ThreadSafeFAISSManager, and
initialize_faiss_with_recovery for GPU/vector mode.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

try:
    import fcntl  # POSIX file locking
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


class FAISSIndexError(RuntimeError):
    """Raised when FAISS index operations fail."""


class FAISSIndexManager:
    """Manages FAISS index lifecycle with incremental updates."""

    def __init__(
        self,
        index_dir: str,
        model_name: str,
        dimension: int,
        session=None,
        session_factory=None,
    ):
        self.index_dir = Path(index_dir)
        self.model_name = model_name
        self.dimension = dimension

        if not session_factory and not session:
            logger.warning(
                "FAISSIndexManager initialized without session access. "
                "Database operations (metadata, rebuild) will fail."
            )

        if session_factory:
            self.session_factory = session_factory
            self.session = None
        else:
            self.session = session
            self.session_factory = None
            if session:
                logger.warning(
                    "Using deprecated single session pattern; prefer session_factory."
                )

        self.index_dir.mkdir(parents=True, exist_ok=True)

        model_slug = (
            model_name.lower()
            .replace("/", "_")
            .replace(":", "_")
            .replace(".", "")
        )
        self.index_filename = f"unified_{model_slug}.index"
        self.meta_filename = f"unified_{model_slug}.meta.json"

        self.index_path = self.index_dir / self.index_filename
        self.meta_path = self.index_dir / self.meta_filename

        self._index = None
        self._faiss = None
        self._lock_path = self.index_dir / "faiss_index.lock"

    @property
    def index(self):
        if self._index is None:
            self._load_or_create_index()
        return self._index

    @property
    def faiss(self):
        if self._faiss is None:
            try:
                import faiss

                self._faiss = faiss
            except ImportError as exc:
                raise FAISSIndexError(
                    "faiss-cpu not installed. Install ML extras with: pip install -e \".[ml]\""
                ) from exc
        return self._faiss

    def _load_or_create_index(self) -> None:
        if self.index_path.exists():
            try:
                logger.info("Loading FAISS index from %s", self.index_path)
                self._index = self.faiss.read_index(str(self.index_path))
                if self._index.d != self.dimension:
                    logger.warning(
                        "Index dimension mismatch: expected %s, got %s",
                        self.dimension,
                        self._index.d,
                    )
            except Exception as exc:
                raise FAISSIndexError(f"Failed to load FAISS index: {exc}") from exc
        else:
            logger.info("FAISS index not found; creating new empty index")
            self._create_new_index(reset_metadata=True)

    def _create_new_index(self, reset_metadata: bool = False) -> None:
        faiss = self.faiss
        # Use a simple inner-product index. We manage ID mapping in SQLite
        # via FAISSMetadata, so we don't need FAISS' IDMap wrapper. This keeps
        # compatibility with faiss-cpu builds that require add_with_ids for IDMap.
        self._index = faiss.IndexFlatIP(self.dimension)
        if reset_metadata:
            self._reset_metadata()

    def _reset_metadata(self) -> None:
        if not (self.session_factory or self.session):
            return
        from src.common.storage.schema import FAISSMetadata

        with self._session_scope() as session:
            session.query(FAISSMetadata).delete()

    @contextmanager
    def _exclusive_lock(self):
        if fcntl is None:
            yield
            return

        self.index_dir.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "a+") as fp:
            try:
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
            except Exception:
                yield
            else:
                try:
                    yield
                finally:
                    try:
                        fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass

    @contextmanager
    def _session_scope(self, read_only: bool = False):
        if self.session_factory:
            session = self.session_factory()
            owns_session = True
        elif self.session:
            session = self.session
            owns_session = False
        else:
            raise FAISSIndexError(
                "No database session available. FAISSIndexManager requires session_factory."
            )

        try:
            yield session
            if owns_session and not read_only:
                session.commit()
            elif not owns_session and not read_only:
                session.flush()
        except Exception as exc:
            try:
                session.rollback()
            except Exception as rollback_error:
                logger.warning("Failed to rollback session: %s", rollback_error)
            raise
        finally:
            if owns_session:
                try:
                    session.close()
                except Exception as close_error:
                    logger.warning("Failed to close session: %s", close_error)

    def add(
        self,
        entity_ids: List[str],
        entity_types: List[str],
        embeddings: np.ndarray,
    ) -> List[int]:
        if (
            len(entity_ids) != len(entity_types)
            or len(entity_ids) != embeddings.shape[0]
        ):
            raise ValueError("Mismatched lengths for entity_ids/entity_types/embeddings")
        if embeddings.shape[1] != self.dimension:
            raise ValueError(
                f"Embedding dimension mismatch: expected {self.dimension}, got {embeddings.shape[1]}"
            )

        try:
            with self._exclusive_lock():
                start_id = self.index.ntotal
                self.index.add(embeddings)
                internal_ids = list(range(start_id, start_id + len(entity_ids)))
                self._save_metadata_mappings(entity_ids, entity_types, internal_ids)
                logger.info(
                    "Added %s vectors to FAISS index (total: %s)",
                    len(entity_ids),
                    self.index.ntotal,
                )
                return internal_ids
        except Exception as exc:
            if self.session:
                try:
                    self.session.rollback()
                except Exception:
                    pass
            raise FAISSIndexError(f"Failed to add vectors to index: {exc}") from exc

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 10,
        entity_type: Optional[str] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        if query_embedding.shape[-1] != self.dimension:
            raise FAISSIndexError(
                f"Query dimension mismatch: expected {self.dimension}, got {query_embedding.shape[-1]}"
            )

        if query_embedding.ndim == 1:
            query_embedding = query_embedding.reshape(1, -1)

        scores, internal_ids = self.index.search(query_embedding, top_k)
        scores = scores[0]
        internal_ids = internal_ids[0]

        if entity_type:
            mask = []
            for internal_id in internal_ids:
                mapping = self.get_entity_id(int(internal_id))
                mask.append(bool(mapping and mapping["entity_type"] == entity_type))
            mask_arr = np.array(mask)
            scores = scores[mask_arr]
            internal_ids = internal_ids[mask_arr]
        return scores, internal_ids

    def get_entity_id(self, internal_id: int):
        metadata = self._load_metadata()
        return metadata.get(str(internal_id))

    def _load_metadata(self) -> Dict[str, Dict[str, str]]:
        if not self.meta_path.exists():
            return {}
        try:
            raw = self.meta_path.read_text("utf-8")
            data = json.loads(raw or "{}")
            if not isinstance(data, dict):
                return {}
            return data
        except Exception as exc:
            logger.warning("Failed to load FAISS metadata %s: %s", self.meta_path, exc)
            return {}

    def _save_metadata_mappings(
        self,
        entity_ids: List[str],
        entity_types: List[str],
        internal_ids: List[int],
    ) -> None:
        from src.common.storage.schema import FAISSMetadata, utc_now

        now = utc_now()
        with self._session_scope() as session:
            for entity_id, entity_type, internal_id in zip(
                entity_ids, entity_types, internal_ids
            ):
                row = FAISSMetadata(
                    entity_id=entity_id,
                    entity_type=entity_type,
                    internal_id=int(internal_id),
                    created_at=now,
                    deleted=False,
                )
                session.add(row)

        metadata = self._load_metadata()
        for entity_id, entity_type, internal_id in zip(
            entity_ids, entity_types, internal_ids
        ):
            metadata[str(internal_id)] = {
                "entity_id": entity_id,
                "entity_type": entity_type,
            }
        self._save_metadata(metadata)

    def _save_metadata(self, metadata: Dict[str, Dict[str, str]]) -> None:
        try:
            tmp = self.meta_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.meta_path)
        except Exception as exc:
            raise FAISSIndexError(f"Failed to save metadata: {exc}") from exc

    def save(self) -> None:
        try:
            self.faiss.write_index(self.index, str(self.index_path))
        except Exception as exc:
            raise FAISSIndexError(f"Failed to save FAISS index: {exc}") from exc

    def _index_hash(self) -> Optional[str]:
        if not self.index_path.exists():
            return None

        h = hashlib.sha256()
        with open(self.index_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def get_tombstone_ratio(self) -> float:
        from src.common.storage.schema import FAISSMetadata

        with self._session_scope(read_only=True) as session:
            total = session.query(FAISSMetadata).count()
            if total == 0:
                return 0.0
            deleted = (
                session.query(FAISSMetadata)
                .filter(FAISSMetadata.deleted == True)  # noqa: E712
                .count()
            )
            return deleted / float(total)

    def needs_rebuild(self) -> bool:
        return self.get_tombstone_ratio() > 0.10

    @property
    def is_available(self) -> bool:
        try:
            _ = self.index
            return True
        except FAISSIndexError:
            return False


class PeriodicSaver:
    """Background thread that periodically saves FAISS index."""

    def __init__(self, faiss_manager: "ThreadSafeFAISSManager", interval: int):
        self.faiss_manager = faiss_manager
        self.interval = interval
        self.dirty = False
        self.stop_event = None
        self.thread = None

    def start(self) -> None:
        import threading

        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run, daemon=True, name="FAISS-PeriodicSaver"
        )
        self.thread.start()
        logger.info("Periodic FAISS saver started (interval=%ss)", self.interval)

    def stop(self) -> None:
        if not self.stop_event or not self.thread:
            return
        self.stop_event.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            logger.warning("Periodic saver thread did not stop cleanly")

    def mark_dirty(self) -> None:
        self.dirty = True

    def _run(self) -> None:
        assert self.stop_event is not None
        while not self.stop_event.wait(self.interval):
            if self.dirty:
                try:
                    self.faiss_manager._save_safely()
                    self.dirty = False
                    logger.debug("Periodic FAISS save completed")
                except Exception as exc:
                    logger.error("Periodic FAISS save failed: %s", exc)


class ThreadSafeFAISSManager:
    """Thread-safe wrapper around FAISSIndexManager."""

    def __init__(
        self,
        faiss_manager: FAISSIndexManager,
        save_policy: str = "immediate",
        save_interval: int = 300,
        rebuild_threshold: float = 0.10,
    ):
        import threading

        self._manager = faiss_manager
        self._lock = threading.RLock()
        self._save_policy = save_policy
        self._rebuild_threshold = rebuild_threshold
        self._periodic_saver: Optional[PeriodicSaver] = None

        if save_policy not in ("immediate", "periodic", "manual"):
            raise ValueError(
                f"Invalid save_policy: {save_policy}. Must be 'immediate', 'periodic', or 'manual'"
            )

        if save_policy == "periodic":
            self._periodic_saver = PeriodicSaver(self, save_interval)
            self._periodic_saver.start()

        logger.info(
            "ThreadSafeFAISSManager initialized: policy=%s, interval=%ss, rebuild_threshold=%s",
            save_policy,
            save_interval,
            rebuild_threshold,
        )

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 10,
        entity_type: Optional[str] = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        with self._lock:
            return self._manager.search(query_embedding, top_k, entity_type)

    def add(
        self,
        entity_ids: List[str],
        entity_types: List[str],
        embeddings: np.ndarray,
    ) -> List[int]:
        with self._lock:
            result = self._manager.add(entity_ids, entity_types, embeddings)
            if self._save_policy == "immediate":
                self._save_safely()
            elif self._save_policy == "periodic" and self._periodic_saver:
                self._periodic_saver.mark_dirty()
            return result

    def save(self) -> None:
        with self._lock:
            self._save_safely()

    def shutdown(self) -> None:
        if self._periodic_saver:
            logger.info("Stopping periodic saver")
            self._periodic_saver.stop()

    def _save_safely(self) -> None:
        tmp_path = self._manager.index_path.with_suffix(".index.tmp")
        backup_path = self._manager.index_path.with_suffix(".index.backup")

        self._manager.faiss.write_index(self._manager.index, str(tmp_path))

        if self._manager.index_path.exists():
            if backup_path.exists():
                backup_path.unlink()
            self._manager.index_path.rename(backup_path)

        tmp_path.rename(self._manager.index_path)
        logger.info("FAISS index saved atomically to %s", self._manager.index_path)

    def get_entity_id(self, internal_id: int):
        with self._lock:
            return self._manager.get_entity_id(internal_id)

    def get_tombstone_ratio(self) -> float:
        with self._lock:
            return self._manager.get_tombstone_ratio()

    def needs_rebuild(self) -> bool:
        with self._lock:
            return self._manager.needs_rebuild()

    @property
    def is_available(self) -> bool:
        with self._lock:
            return self._manager.is_available

    def __getattr__(self, name):
        attr = getattr(self._manager, name)
        if callable(attr):
            def locked_method(*args, **kwargs):
                with self._lock:
                    return attr(*args, **kwargs)

            return locked_method
        return attr


def initialize_faiss_with_recovery(
    config,
    session,
    embedding_client=None,
    session_factory=None,
) -> Optional[FAISSIndexManager]:
    """Initialize FAISS index with automatic recovery."""
    from src.common.storage.schema import Embedding, FAISSMetadata, utc_now

    dimension = getattr(embedding_client, "dimension", 768) if embedding_client else 768

    try:
        model_name = config.embedding_model
        faiss_manager = FAISSIndexManager(
            index_dir=str(config.faiss_index_path),
            model_name=model_name,
            dimension=dimension,
            session=session,
            session_factory=session_factory,
        )
    except Exception as exc:
        logger.error("Failed to create FAISSIndexManager: %s", exc)
        return None

    try:
        _ = faiss_manager.index
        logger.info("FAISS index loaded successfully: %s vectors", faiss_manager.index.ntotal)

        if faiss_manager.needs_rebuild():
            logger.warning("Index inconsistency detected, triggering automatic rebuild")
            metadata_list = (
                session.query(FAISSMetadata)
                .filter(FAISSMetadata.deleted == False)
                .all()
            )
            if not metadata_list:
                logger.warning("No non-deleted entries found for rebuild, creating empty index")
                faiss_manager._create_new_index(reset_metadata=True)
                faiss_manager.save()
                session.flush()
            else:
                from src.common.storage.repository import EmbeddingRepository

                emb_repo = EmbeddingRepository(session)
                metadata_map = {
                    meta.entity_id: meta.entity_type for meta in metadata_list
                }
                embeddings = (
                    session.query(Embedding)
                    .filter(
                        Embedding.model_version == model_name,
                        Embedding.entity_id.in_(list(metadata_map.keys())),
                    )
                    .all()
                )
                if not embeddings:
                    logger.warning("No embeddings found for non-deleted entries")
                    faiss_manager._create_new_index(reset_metadata=True)
                    faiss_manager.save()
                    session.flush()
                else:
                    entity_ids: List[str] = []
                    entity_types: List[str] = []
                    vectors: List[np.ndarray] = []
                    for emb in embeddings:
                        if emb.entity_id in metadata_map:
                            entity_ids.append(emb.entity_id)
                            entity_types.append(metadata_map[emb.entity_id])
                            vectors.append(emb_repo.to_numpy(emb))
                    if entity_ids:
                        faiss_manager._create_new_index(reset_metadata=True)
                        vectors_array = np.vstack(vectors).astype(np.float32)
                        faiss_manager.add(entity_ids, entity_types, vectors_array)
                        faiss_manager.save()
                        session.flush()
            return faiss_manager

        return faiss_manager
    except FAISSIndexError as exc:
        logger.warning("Failed to load FAISS index: %s", exc)

        backup_path = faiss_manager.index_path.with_suffix(".index.backup")
        if backup_path.exists():
            try:
                logger.info("Attempting to restore FAISS index from backup")
                import shutil

                shutil.copy2(backup_path, faiss_manager.index_path)
                faiss_manager._index = None
                _ = faiss_manager.index
                logger.info(
                    "FAISS index restored from backup: %s vectors",
                    faiss_manager.index.ntotal,
                )
                return faiss_manager
            except Exception as exc2:
                logger.error("Backup restore failed: %s", exc2)

        try:
            logger.info("Attempting to rebuild FAISS index from database")
            from src.common.storage.repository import EmbeddingRepository

            emb_repo = EmbeddingRepository(session)
            faiss_manager._create_new_index(reset_metadata=True)
            embeddings = (
                session.query(Embedding)
                .filter(Embedding.model_version == model_name)
                .all()
            )
            if not embeddings:
                logger.warning("No embeddings found in database, starting with empty index")
                faiss_manager.save()
                return faiss_manager

            entity_type_map: Dict[str, str] = {}
            for emb in embeddings:
                if emb.entity_id.startswith("EXP-"):
                    entity_type_map[emb.entity_id] = "experience"
                elif emb.entity_id.startswith("MNL-"):
                    entity_type_map[emb.entity_id] = "skill"

            entity_ids: List[str] = []
            entity_types: List[str] = []
            vectors: List[np.ndarray] = []
            for emb in embeddings:
                if emb.entity_id in entity_type_map:
                    entity_ids.append(emb.entity_id)
                    entity_types.append(entity_type_map[emb.entity_id])
                    vectors.append(emb_repo.to_numpy(emb))

            if entity_ids:
                vectors_array = np.vstack(vectors).astype(np.float32)
                faiss_manager.add(entity_ids, entity_types, vectors_array)
                faiss_manager.save()
                session.flush()
                logger.info(
                    "FAISS index rebuilt successfully: %s vectors", len(entity_ids)
                )
            else:
                logger.warning("No valid entities found for rebuild")
                faiss_manager.save()

            return faiss_manager
        except Exception as exc3:
            logger.error("Index rebuild failed: %s", exc3)
            logger.warning("FAISS will be unavailable, falling back to text search")
            return None


__all__ = [
    "FAISSIndexManager",
    "FAISSIndexError",
    "ThreadSafeFAISSManager",
    "initialize_faiss_with_recovery",
]
