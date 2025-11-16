"""Background worker for automatic embedding processing.

This module provides a background thread that continuously processes
pending embeddings from the database queue.
"""
import logging
import threading
import time
from typing import Optional, Dict, Any, Callable
from datetime import datetime, timezone
import os
import socket
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.gpu.embedding_service import EmbeddingService
from src.api.gpu.embedding_client import EmbeddingClient


logger = logging.getLogger(__name__)


class BackgroundEmbeddingWorker:
    """Background worker that automatically processes pending embeddings.

    The worker runs in a separate daemon thread and polls the database
    for pending embeddings at regular intervals. It uses the existing
    EmbeddingService to process entries and update the FAISS index.

    Thread-safety: This worker creates its own database sessions per batch
    to avoid conflicts with the main API thread.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        embedding_client: EmbeddingClient,
        model_name: str,
        faiss_manager: Optional[Any] = None,
        poll_interval: float = 5.0,
        batch_size: int = 10,
        max_tokens: int = 8000,
    ):
        """Initialize background worker.

        Args:
            session_factory: Factory function that returns a new database session
            embedding_client: Client for generating embeddings
            model_name: Full model name in 'repo:quant' format (from config.embedding_model)
            faiss_manager: Optional FAISS manager for index updates
            poll_interval: Seconds to wait between polls (default: 5.0)
            batch_size: Maximum number of entries to process per batch (default: 10)
            max_tokens: Max tokens for manual content (default: 8000)
        """
        self.session_factory = session_factory
        self.embedding_client = embedding_client
        self.model_name = model_name
        self.faiss_manager = faiss_manager
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self.max_tokens = max_tokens

        # Worker state
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._paused = False
        self._pause_lock = threading.Lock()

        # Statistics
        self._stats = {
            'total_processed': 0,
            'total_succeeded': 0,
            'total_failed': 0,
            'last_run': None,
            'last_batch_size': 0,
            'is_running': False,
            'is_paused': False,
        }
        self._stats_lock = threading.Lock()

        # Leader election (single active worker across processes)
        self._lease_name = "embedding-worker"
        host = socket.gethostname()
        self._lease_owner = f"{host}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._lease_ttl = float(os.getenv("CHL_WORKER_LEASE_TTL", "30"))  # seconds
        self._lease_next_refresh: float = 0.0
        self._lease_held: bool = False

    def start(self):
        """Start the background worker thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Background worker already running")
            return

        logger.info(
            "Starting background embedding worker "
            f"(poll_interval={self.poll_interval}s, batch_size={self.batch_size})"
        )

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="embedding-worker",
            daemon=True,  # Allow process to exit even if thread is running
        )
        self._thread.start()

        with self._stats_lock:
            self._stats['is_running'] = True

    def stop(self, timeout: float = 10.0):
        """Stop the background worker thread.

        Args:
            timeout: Maximum seconds to wait for thread to finish (default: 10.0)
        """
        if self._thread is None or not self._thread.is_alive():
            logger.warning("Background worker not running")
            return

        logger.info("Stopping background embedding worker...")
        self._stop_event.set()
        self._thread.join(timeout=timeout)

        if self._thread.is_alive():
            logger.warning(f"Background worker did not stop within {timeout}s")

        with self._stats_lock:
            self._stats['is_running'] = False
        # Best-effort: release leader lease
        try:
            self._release_lease()
        except Exception:
            pass

    def pause(self):
        """Pause the worker (stops processing but keeps thread alive)."""
        with self._pause_lock:
            self._paused = True

        with self._stats_lock:
            self._stats['is_paused'] = True

        logger.info("Background worker paused")

    def resume(self):
        """Resume the worker after pause."""
        with self._pause_lock:
            self._paused = False

        with self._stats_lock:
            self._stats['is_paused'] = False

        logger.info("Background worker resumed")

    def is_running(self) -> bool:
        """Check if worker thread is running."""
        return self._thread is not None and self._thread.is_alive()

    def is_paused(self) -> bool:
        """Check if worker is paused."""
        with self._pause_lock:
            return self._paused

    def get_stats(self) -> Dict[str, Any]:
        """Get worker statistics.

        Returns:
            Dictionary with worker stats including processed counts and status
        """
        with self._stats_lock:
            return self._stats.copy()

    def _worker_loop(self):
        """Main worker loop (runs in background thread)."""
        logger.info("Background worker loop started")

        while not self._stop_event.is_set():
            try:
                # Leader election: ensure we hold the lease before processing
                if not self._ensure_lease():
                    # Not the leader; sleep a bit before trying again
                    with self._stats_lock:
                        self._stats['is_paused'] = True
                    time.sleep(min(2.0, self.poll_interval))
                    continue
                # Check if paused
                with self._pause_lock:
                    if self._paused:
                        # Sleep briefly and continue (don't process while paused)
                        time.sleep(1.0)
                        continue

                # Process one batch
                batch_stats = self._process_batch()

                # Update overall statistics
                with self._stats_lock:
                    self._stats['total_processed'] += batch_stats['processed']
                    self._stats['total_succeeded'] += batch_stats['succeeded']
                    self._stats['total_failed'] += batch_stats['failed']
                    self._stats['last_run'] = datetime.utcnow().isoformat()
                    self._stats['last_batch_size'] = batch_stats['processed']

                # Log batch completion if we processed anything
                if batch_stats['processed'] > 0:
                    logger.info(
                        f"Processed batch: {batch_stats['processed']} entries "
                        f"({batch_stats['succeeded']} succeeded, {batch_stats['failed']} failed)"
                    )

            except Exception as e:
                logger.error(f"Error in worker loop: {e}", exc_info=True)

            # Sleep until next poll (check stop event periodically)
            for _ in range(int(self.poll_interval * 10)):
                if self._stop_event.is_set():
                    break
                time.sleep(0.1)

        logger.info("Background worker loop stopped")
        # Best-effort: release lease on exit
        try:
            self._release_lease()
        except Exception:
            pass

    def _process_batch(self) -> Dict[str, int]:
        """Process one batch of pending embeddings.

        Returns:
            Dictionary with counts: {'processed': N, 'succeeded': M, 'failed': K}
        """
        # Create a new session for this batch
        session = self.session_factory()

        try:
            # Create embedding service for this batch
            embedding_service = EmbeddingService(
                session=session,
                embedding_client=self.embedding_client,
                model_name=self.model_name,
                faiss_index_manager=self.faiss_manager,
                max_tokens=self.max_tokens,
            )

            # Process pending embeddings (limited by batch_size)
            stats = embedding_service.process_pending(max_count=self.batch_size)

            return stats

        except Exception as e:
            logger.error(f"Error processing batch: {e}", exc_info=True)
            return {'processed': 0, 'succeeded': 0, 'failed': 0}

        finally:
            # Always close the session
            try:
                session.close()
            except Exception as e:
                logger.warning(f"Error closing session: {e}")

    # ------------------------------------------------------------------
    # Lease helpers (leader election)
    # ------------------------------------------------------------------
    def _ensure_lease(self) -> bool:
        """Acquire or renew the lease; returns True if we are the leader.

        Uses operation_locks(name=self._lease_name) as a distributed lock with TTL.
        """
        now = time.time()
        # Refresh early (half TTL) to avoid expiry
        if self._lease_held and now < self._lease_next_refresh:
            return True

        session = self.session_factory()
        try:
            from src.common.storage.schema import OperationLock, utc_now
            # Load existing lock
            lock = (
                session.query(OperationLock)
                .filter(OperationLock.name == self._lease_name)
                .one_or_none()
            )

            # Compute expiry
            from datetime import timedelta
            now_dt = datetime.now(timezone.utc)
            next_expiry = now_dt + timedelta(seconds=self._lease_ttl)

            def _commit_refresh():
                session.flush()
                session.commit()

            if lock is None:
                # Try to create the lock
                try:
                    lock = OperationLock(
                        name=self._lease_name,
                        owner=self._lease_owner,
                        created_at=utc_now(),
                        expires_at=next_expiry.isoformat(),
                    )
                    session.add(lock)
                    _commit_refresh()
                    self._lease_held = True
                except IntegrityError:
                    session.rollback()
                    self._lease_held = False
            else:
                # Parse expiry
                expires_at = None
                try:
                    if lock.expires_at:
                        expires_at = datetime.fromisoformat(lock.expires_at)
                except Exception:
                    expires_at = None

                if lock.owner == self._lease_owner or (expires_at is None or expires_at <= now_dt):
                    # Take over or renew
                    lock.owner = self._lease_owner
                    lock.expires_at = next_expiry.isoformat()
                    lock.created_at = utc_now()
                    _commit_refresh()
                    self._lease_held = True
                else:
                    # Another active owner
                    self._lease_held = False

            # Schedule next refresh (half TTL)
            if self._lease_held:
                self._lease_next_refresh = time.time() + max(1.0, self._lease_ttl * 0.5)
                with self._stats_lock:
                    self._stats['is_paused'] = False
            else:
                with self._stats_lock:
                    self._stats['is_paused'] = True

            return self._lease_held

        except Exception as e:
            # Fail-open: if lease check fails, do not process to avoid split-brain
            logger.debug(f"Lease check failed, deferring processing: {e}")
            try:
                session.rollback()
            except Exception:
                pass
            self._lease_held = False
            with self._stats_lock:
                self._stats['is_paused'] = True
            return False
        finally:
            try:
                session.close()
            except Exception:
                pass

    def _release_lease(self) -> None:
        if not self._lease_held:
            return
        session = self.session_factory()
        try:
            from src.common.storage.schema import OperationLock
            lock = (
                session.query(OperationLock)
                .filter(
                    OperationLock.name == self._lease_name,
                    OperationLock.owner == self._lease_owner,
                )
                .one_or_none()
            )

            if lock:
                session.delete(lock)
                session.commit()
        except Exception:
            try:
                session.rollback()
            except Exception:
                pass
        finally:
            try:
                session.close()
            except Exception:
                pass
        self._lease_held = False


class WorkerPool:
    """Simple worker pool wrapper for compatibility with existing code.

    This provides the same interface as the legacy worker pool but uses
    the new BackgroundEmbeddingWorker implementation.
    """

    def __init__(self, worker: BackgroundEmbeddingWorker):
        """Initialize worker pool with a single background worker.

        Args:
            worker: The background embedding worker instance
        """
        self.worker = worker

    def pause_all(self):
        """Pause all workers (just one worker in our case)."""
        self.worker.pause()

    def resume_all(self):
        """Resume all workers."""
        self.worker.resume()

    def get_status(self) -> Dict[str, Any]:
        """Get worker pool status.

        Returns:
            Dictionary with pool status matching legacy format
        """
        stats = self.worker.get_stats()

        # Build worker object matching telemetry expectations
        worker_obj = {
            'worker_id': 'embedding-worker-0',
            'running': stats['is_running'],
            'paused': stats['is_paused'],
            'jobs_processed': stats['total_succeeded'],
            'jobs_failed': stats['total_failed'],
            'last_run': stats['last_run'],
            'last_batch_size': stats['last_batch_size'],
        }

        return {
            'active_workers': 1 if stats['is_running'] and not stats['is_paused'] else 0,
            'total_workers': 1,
            'is_paused': stats['is_paused'],
            'stats': stats,
            'workers': [worker_obj] if stats['is_running'] else [],
        }


__all__ = ["BackgroundEmbeddingWorker", "WorkerPool"]
