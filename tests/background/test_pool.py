"""Unit tests for WorkerPool."""

import pytest
import time

from src.background.pool import WorkerPool
from src.storage.schema import Experience, CategoryManual


def test_pool_initialization(temp_db, mock_embedding_service):
    """Test worker pool can be initialized."""
    pool = WorkerPool(
        num_workers=3,
        session_factory=temp_db.get_session,
        embedding_service=mock_embedding_service,
        poll_interval=1,
        batch_size=5
    )

    assert pool.num_workers == 3
    assert len(pool.workers) == 3
    assert all(not w.running for w in pool.workers)


def test_pool_start_stop_all(temp_db, mock_embedding_service):
    """Test starting and stopping all workers in pool."""
    pool = WorkerPool(
        num_workers=2,
        session_factory=temp_db.get_session,
        embedding_service=mock_embedding_service,
        poll_interval=1,
        batch_size=5
    )

    # Start all workers
    pool.start_all()
    assert all(w.running for w in pool.workers)
    assert all(w.thread.is_alive() for w in pool.workers)

    # Stop all workers
    pool.stop_all(timeout=5)
    assert all(not w.running for w in pool.workers)


def test_pool_pause_resume_all(temp_db, mock_embedding_service):
    """Test pausing and resuming all workers in pool."""
    pool = WorkerPool(
        num_workers=2,
        session_factory=temp_db.get_session,
        embedding_service=mock_embedding_service,
        poll_interval=1,
        batch_size=5
    )

    # Start workers
    pool.start_all()

    # Pause all
    pool.pause_all()
    assert all(w.paused_event.is_set() for w in pool.workers)

    # Resume all
    pool.resume_all()
    assert all(not w.paused_event.is_set() for w in pool.workers)

    pool.stop_all(timeout=5)


def test_pool_get_status(temp_db, mock_embedding_service):
    """Test getting status of all workers."""
    pool = WorkerPool(
        num_workers=2,
        session_factory=temp_db.get_session,
        embedding_service=mock_embedding_service,
        poll_interval=1,
        batch_size=5
    )

    status = pool.get_status()
    assert "num_workers" in status
    assert status["num_workers"] == 2
    assert "workers" in status
    assert len(status["workers"]) == 2
    assert all("worker_id" in w for w in status["workers"])
    assert all("running" in w for w in status["workers"])
    assert all("jobs_processed" in w for w in status["workers"])


def test_pool_get_queue_depth(temp_db, mock_embedding_service, sample_experiences, sample_manuals):
    """Test getting queue depth statistics."""
    pool = WorkerPool(
        num_workers=1,
        session_factory=temp_db.get_session,
        embedding_service=mock_embedding_service,
        poll_interval=1,
        batch_size=5
    )

    depth = pool.get_queue_depth()

    assert "pending" in depth
    assert "failed" in depth
    assert depth["pending"]["experiences"] == len(sample_experiences)
    assert depth["pending"]["manuals"] == len(sample_manuals)
    assert depth["pending"]["total"] == len(sample_experiences) + len(sample_manuals)
    assert depth["failed"]["total"] == 0


def test_pool_multiple_workers_process_queue(temp_db, mock_embedding_service, sample_experiences):
    """Test multiple workers can process queue concurrently."""
    pool = WorkerPool(
        num_workers=2,
        session_factory=temp_db.get_session,
        embedding_service=mock_embedding_service,
        poll_interval=0.5,
        batch_size=3
    )

    # Start workers
    pool.start_all()

    # Wait for processing
    time.sleep(3)

    # Stop workers
    pool.stop_all(timeout=5)

    # Verify queue was processed
    depth = pool.get_queue_depth()
    assert depth["pending"]["total"] == 0

    # Verify work was distributed (both workers should have processed something)
    status = pool.get_status()
    total_processed = sum(w["jobs_processed"] for w in status)
    assert total_processed == len(sample_experiences)


def test_pool_handles_worker_failure(temp_db, sample_experiences):
    """Test pool continues working when one worker encounters errors."""
    # Create a service that fails on first worker but succeeds on others
    call_count = [0]

    def upsert_experience(exp_id):
        call_count[0] += 1
        if call_count[0] <= 2:  # First 2 calls fail
            raise Exception("Simulated failure")
        return True

    from unittest.mock import Mock
    failing_service = Mock()
    failing_service.upsert_for_experience = Mock(side_effect=upsert_experience)

    pool = WorkerPool(
        num_workers=2,
        session_factory=temp_db.get_session,
        embedding_service=failing_service,
        poll_interval=0.5,
        batch_size=2
    )

    # Start workers
    pool.start_all()

    # Wait for processing
    time.sleep(3)

    # Stop workers
    pool.stop_all(timeout=5)

    # Verify some jobs succeeded and some failed
    status = pool.get_status()
    total_failed = sum(w["jobs_failed"] for w in status)
    total_succeeded = sum(w["jobs_succeeded"] for w in status)

    assert total_failed > 0  # Some should have failed
    assert total_succeeded > 0  # Some should have succeeded


def test_pool_queue_depth_after_failures(temp_db, sample_experiences):
    """Test queue depth correctly tracks failed jobs."""
    from unittest.mock import Mock

    # Create a service that always fails
    failing_service = Mock()
    failing_service.upsert_for_experience = Mock(side_effect=Exception("Always fails"))

    pool = WorkerPool(
        num_workers=1,
        session_factory=temp_db.get_session,
        embedding_service=failing_service,
        poll_interval=0.5,
        batch_size=10
    )

    # Start workers
    pool.start_all()

    # Wait for processing
    time.sleep(2)

    # Stop workers
    pool.stop_all(timeout=5)

    # Verify all jobs are marked as failed
    depth = pool.get_queue_depth()
    assert depth["pending"]["total"] == 0
    assert depth["failed"]["total"] == len(sample_experiences)
    assert depth["failed"]["experiences"] == len(sample_experiences)
