"""Worker pool manager for embedding workers."""
import logging
from typing import List, Optional

from .worker import EmbeddingWorker

logger = logging.getLogger(__name__)


class WorkerPool:
    """
    Manages a pool of embedding workers.

    Features:
    - Start/stop all workers
    - Pause/resume all workers (for maintenance)
    - Monitor worker health
    - Automatic restart on failure
    """

    def __init__(
        self,
        num_workers: int,
        session_factory,
        embedding_service,
        poll_interval: int = 5,
        batch_size: int = 10,
    ):
        """Initialize worker pool.

        Args:
            num_workers: Number of workers in the pool
            session_factory: Callable that returns a new SQLAlchemy session
            embedding_service: EmbeddingService instance
            poll_interval: Seconds between queue polls
            batch_size: Maximum entries to process per batch
        """
        self.num_workers = num_workers
        self.session_factory = session_factory
        self.embedding_service = embedding_service
        self.poll_interval = poll_interval
        self.batch_size = batch_size

        self.workers: List[EmbeddingWorker] = []
        self._init_workers()

    def _init_workers(self):
        """Initialize worker instances."""
        for i in range(self.num_workers):
            worker = EmbeddingWorker(
                worker_id=i,
                session_factory=self.session_factory,
                embedding_service=self.embedding_service,
                poll_interval=self.poll_interval,
                batch_size=self.batch_size,
            )
            self.workers.append(worker)

    def start_all(self):
        """Start all workers."""
        logger.info(f"Starting {self.num_workers} workers")
        for worker in self.workers:
            worker.start()

    def stop_all(self, timeout: int = 30):
        """Stop all workers gracefully.

        Args:
            timeout: Maximum seconds to wait for each worker to stop
        """
        logger.info("Stopping all workers")
        for worker in self.workers:
            worker.stop(timeout=timeout)

    def pause_all(self):
        """Pause all workers."""
        logger.info("Pausing all workers")
        for worker in self.workers:
            worker.pause()

    def resume_all(self):
        """Resume all workers."""
        logger.info("Resuming all workers")
        for worker in self.workers:
            worker.resume()

    def get_status(self) -> dict:
        """Get status of all workers.

        Returns:
            Dictionary with pool and individual worker status
        """
        return {
            "num_workers": self.num_workers,
            "workers": [w.get_status() for w in self.workers],
            "total_jobs_processed": sum(w.jobs_processed for w in self.workers),
            "total_jobs_succeeded": sum(w.jobs_succeeded for w in self.workers),
            "total_jobs_failed": sum(w.jobs_failed for w in self.workers),
        }

    def get_queue_depth(self) -> dict:
        """Get current queue depth from database.

        Returns:
            Dictionary with pending and failed job counts
        """
        from src.storage.schema import Experience, CategoryManual

        session = self.session_factory()
        try:
            pending_exp = session.query(Experience).filter(
                Experience.embedding_status == 'pending'
            ).count()

            pending_man = session.query(CategoryManual).filter(
                CategoryManual.embedding_status == 'pending'
            ).count()

            failed_exp = session.query(Experience).filter(
                Experience.embedding_status == 'failed'
            ).count()

            failed_man = session.query(CategoryManual).filter(
                CategoryManual.embedding_status == 'failed'
            ).count()

            return {
                "pending": {
                    "experiences": pending_exp,
                    "manuals": pending_man,
                    "total": pending_exp + pending_man,
                },
                "failed": {
                    "experiences": failed_exp,
                    "manuals": failed_man,
                    "total": failed_exp + failed_man,
                },
            }
        finally:
            session.close()
