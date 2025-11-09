"""Background worker for automatic embedding processing.

This module provides a background thread that continuously processes
pending embeddings from the database queue.
"""
import logging
import threading
import time
from typing import Optional, Dict, Any, Callable
from datetime import datetime

from sqlalchemy.orm import Session

from src.embedding.service import EmbeddingService
from src.embedding.client import EmbeddingClient


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
        faiss_manager: Optional[Any] = None,
        poll_interval: float = 5.0,
        batch_size: int = 10,
        max_tokens: int = 8000,
    ):
        """Initialize background worker.

        Args:
            session_factory: Factory function that returns a new database session
            embedding_client: Client for generating embeddings
            faiss_manager: Optional FAISS manager for index updates
            poll_interval: Seconds to wait between polls (default: 5.0)
            batch_size: Maximum number of entries to process per batch (default: 10)
            max_tokens: Max tokens for manual content (default: 8000)
        """
        self.session_factory = session_factory
        self.embedding_client = embedding_client
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
        else:
            logger.info("Background worker stopped")

        with self._stats_lock:
            self._stats['is_running'] = False

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
