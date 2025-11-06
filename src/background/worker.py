"""Background worker for processing embedding queue."""
import logging
import threading
import time
from typing import Optional, Callable
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class EmbeddingWorker:
    """
    Background worker that processes pending embeddings.

    Behavior:
    - Polls database for entries with embedding_status='pending'
    - Processes embeddings in batches
    - Updates FAISS index after successful embedding
    - Marks entries as 'embedded' or 'failed'
    - Automatically restarts on failure
    """

    def __init__(
        self,
        worker_id: int,
        session_factory: Callable,
        embedding_service,
        poll_interval: int = 5,
        batch_size: int = 10,
    ):
        """Initialize embedding worker.

        Args:
            worker_id: Unique worker identifier
            session_factory: Callable that returns a new SQLAlchemy session
            embedding_service: EmbeddingService instance for generating embeddings
            poll_interval: Seconds between queue polls (default: 5)
            batch_size: Maximum entries to process per batch (default: 10)
        """
        self.worker_id = worker_id
        self.session_factory = session_factory
        self.embedding_service = embedding_service
        self.poll_interval = poll_interval
        self.batch_size = batch_size

        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.paused_event = threading.Event()
        self.running = False

        # Metrics
        self.jobs_processed = 0
        self.jobs_succeeded = 0
        self.jobs_failed = 0
        self.last_run = None

    def start(self):
        """Start worker thread."""
        if self.running:
            logger.warning(f"Worker {self.worker_id} already running")
            return

        self.stop_event.clear()
        self.paused_event.clear()
        self.running = True

        self.thread = threading.Thread(
            target=self._run,
            name=f"embedding-worker-{self.worker_id}",
            daemon=True
        )
        self.thread.start()
        logger.info(f"Worker {self.worker_id} started")

    def stop(self, timeout: int = 30):
        """Stop worker thread gracefully.

        Args:
            timeout: Maximum seconds to wait for worker to stop
        """
        if not self.running:
            return

        logger.info(f"Stopping worker {self.worker_id}")
        self.stop_event.set()

        if self.thread:
            self.thread.join(timeout=timeout)
            if self.thread.is_alive():
                logger.warning(f"Worker {self.worker_id} did not stop within {timeout}s")

        self.running = False
        logger.info(f"Worker {self.worker_id} stopped")

    def pause(self):
        """Pause worker (stop processing but don't exit)."""
        logger.info(f"Pausing worker {self.worker_id}")
        self.paused_event.set()

    def resume(self):
        """Resume worker."""
        logger.info(f"Resuming worker {self.worker_id}")
        self.paused_event.clear()

    def _run(self):
        """Main worker loop."""
        while not self.stop_event.is_set():
            # Check if paused
            if self.paused_event.is_set():
                time.sleep(1)
                continue

            try:
                self._process_batch()
            except Exception as e:
                logger.error(f"Worker {self.worker_id} error: {e}", exc_info=True)
                time.sleep(self.poll_interval)

            # Sleep until next poll
            time.sleep(self.poll_interval)

    def _process_batch(self):
        """Process one batch of pending embeddings."""
        from src.storage.schema import Experience, CategoryManual

        session = self.session_factory()
        try:
            # Get pending experiences
            pending_exp = session.query(Experience).filter(
                Experience.embedding_status == 'pending'
            ).limit(self.batch_size).all()

            # Get pending manuals
            pending_man = session.query(CategoryManual).filter(
                CategoryManual.embedding_status == 'pending'
            ).limit(self.batch_size - len(pending_exp)).all()

            if not pending_exp and not pending_man:
                # No work to do
                self.last_run = time.time()
                return

            logger.info(
                f"Worker {self.worker_id} processing {len(pending_exp)} experiences, "
                f"{len(pending_man)} manuals"
            )

            # Process experiences
            for exp in pending_exp:
                try:
                    # Use upsert to handle idempotency
                    success = self.embedding_service.upsert_for_experience(exp.id)
                    session.commit()  # Commit after each to avoid long transactions

                    self.jobs_processed += 1
                    if success:
                        self.jobs_succeeded += 1
                    else:
                        self.jobs_failed += 1

                except Exception as e:
                    logger.error(f"Failed to process experience {exp.id}: {e}")
                    session.rollback()

                    # Mark as failed
                    try:
                        exp.embedding_status = 'failed'
                        session.commit()
                        self.jobs_failed += 1
                    except Exception:
                        session.rollback()

            # Process manuals
            for man in pending_man:
                try:
                    success = self.embedding_service.upsert_for_manual(man.id)
                    session.commit()

                    self.jobs_processed += 1
                    if success:
                        self.jobs_succeeded += 1
                    else:
                        self.jobs_failed += 1

                except Exception as e:
                    logger.error(f"Failed to process manual {man.id}: {e}")
                    session.rollback()

                    try:
                        man.embedding_status = 'failed'
                        session.commit()
                        self.jobs_failed += 1
                    except Exception:
                        session.rollback()

            self.last_run = time.time()

        finally:
            session.close()

    def get_status(self) -> dict:
        """Get worker status for monitoring.

        Returns:
            Dictionary with worker status metrics
        """
        return {
            "worker_id": self.worker_id,
            "running": self.running,
            "paused": self.paused_event.is_set(),
            "jobs_processed": self.jobs_processed,
            "jobs_succeeded": self.jobs_succeeded,
            "jobs_failed": self.jobs_failed,
            "last_run": self.last_run,
        }
