"""Unit tests for EmbeddingWorker."""

import pytest
import time
from unittest.mock import Mock, patch

from src.background.worker import EmbeddingWorker
from src.storage.schema import Experience, CategoryManual


def test_worker_initialization(temp_db, mock_embedding_service):
    """Test worker can be initialized with required parameters."""
    worker = EmbeddingWorker(
        worker_id=1,
        session_factory=temp_db.get_session,
        embedding_service=mock_embedding_service,
        poll_interval=1,
        batch_size=5
    )

    assert worker.worker_id == 1
    assert worker.poll_interval == 1
    assert worker.batch_size == 5
    assert worker.running is False
    assert worker.jobs_processed == 0
    assert worker.jobs_succeeded == 0
    assert worker.jobs_failed == 0


def test_worker_start_stop(temp_db, mock_embedding_service):
    """Test worker can be started and stopped."""
    worker = EmbeddingWorker(
        worker_id=1,
        session_factory=temp_db.get_session,
        embedding_service=mock_embedding_service,
        poll_interval=1,
        batch_size=5
    )

    # Start worker
    worker.start()
    assert worker.running is True
    assert worker.thread is not None
    assert worker.thread.is_alive()

    # Stop worker
    worker.stop(timeout=5)
    assert worker.running is False


def test_worker_processes_pending_experiences(temp_db, mock_embedding_service, sample_experiences):
    """Test worker processes pending experiences."""
    worker = EmbeddingWorker(
        worker_id=1,
        session_factory=temp_db.get_session,
        embedding_service=mock_embedding_service,
        poll_interval=0.5,
        batch_size=10
    )

    # Start worker
    worker.start()

    # Wait for processing
    time.sleep(2)

    # Stop worker
    worker.stop(timeout=5)

    # Verify all experiences were processed
    assert mock_embedding_service.upsert_for_experience.call_count == len(sample_experiences)
    assert worker.jobs_processed == len(sample_experiences)
    assert worker.jobs_succeeded == len(sample_experiences)
    assert worker.jobs_failed == 0

    # Verify experiences are marked as embedded
    with temp_db.session_scope() as session:
        pending = session.query(Experience).filter(
            Experience.embedding_status == 'pending'
        ).count()
        assert pending == 0


def test_worker_processes_pending_manuals(temp_db, mock_embedding_service, sample_manuals):
    """Test worker processes pending manuals."""
    worker = EmbeddingWorker(
        worker_id=1,
        session_factory=temp_db.get_session,
        embedding_service=mock_embedding_service,
        poll_interval=0.5,
        batch_size=10
    )

    # Start worker
    worker.start()

    # Wait for processing
    time.sleep(2)

    # Stop worker
    worker.stop(timeout=5)

    # Verify all manuals were processed
    assert mock_embedding_service.upsert_for_manual.call_count == len(sample_manuals)
    assert worker.jobs_processed == len(sample_manuals)
    assert worker.jobs_succeeded == len(sample_manuals)
    assert worker.jobs_failed == 0

    # Verify manuals are marked as embedded
    with temp_db.session_scope() as session:
        pending = session.query(CategoryManual).filter(
            CategoryManual.embedding_status == 'pending'
        ).count()
        assert pending == 0


def test_worker_handles_embedding_failure(temp_db, sample_experiences):
    """Test worker marks entries as failed when embedding fails."""
    # Create mock service that raises exception
    failing_service = Mock()
    failing_service.upsert_for_experience = Mock(side_effect=Exception("Embedding failed"))

    worker = EmbeddingWorker(
        worker_id=1,
        session_factory=temp_db.get_session,
        embedding_service=failing_service,
        poll_interval=0.5,
        batch_size=10
    )

    # Start worker
    worker.start()

    # Wait for processing
    time.sleep(2)

    # Stop worker
    worker.stop(timeout=5)

    # Verify entries were marked as failed
    assert worker.jobs_processed == len(sample_experiences)
    assert worker.jobs_failed == len(sample_experiences)
    assert worker.jobs_succeeded == 0

    # Verify experiences are marked as failed in database
    with temp_db.session_scope() as session:
        failed = session.query(Experience).filter(
            Experience.embedding_status == 'failed'
        ).count()
        assert failed == len(sample_experiences)


def test_worker_pause_resume(temp_db, mock_embedding_service, sample_experiences):
    """Test worker can be paused and resumed."""
    worker = EmbeddingWorker(
        worker_id=1,
        session_factory=temp_db.get_session,
        embedding_service=mock_embedding_service,
        poll_interval=0.5,
        batch_size=10
    )

    # Start worker
    worker.start()

    # Pause immediately
    worker.pause()
    time.sleep(1)

    # Verify no processing happened while paused
    initial_processed = worker.jobs_processed
    time.sleep(1)
    assert worker.jobs_processed == initial_processed

    # Resume and wait for processing
    worker.resume()
    time.sleep(2)

    # Stop worker
    worker.stop(timeout=5)

    # Verify processing happened after resume
    assert worker.jobs_processed > initial_processed


def test_worker_batch_size_limit(temp_db, mock_embedding_service, sample_experiences):
    """Test worker respects batch size limit."""
    batch_size = 2

    worker = EmbeddingWorker(
        worker_id=1,
        session_factory=temp_db.get_session,
        embedding_service=mock_embedding_service,
        poll_interval=0.5,
        batch_size=batch_size
    )

    # Process one batch
    worker._process_batch()

    # Verify only batch_size items were processed
    # (could be less if there are fewer pending items)
    assert mock_embedding_service.upsert_for_experience.call_count <= batch_size


def test_worker_get_status(temp_db, mock_embedding_service):
    """Test worker status reporting."""
    worker = EmbeddingWorker(
        worker_id=42,
        session_factory=temp_db.get_session,
        embedding_service=mock_embedding_service,
        poll_interval=1,
        batch_size=5
    )

    status = worker.get_status()

    assert status["worker_id"] == 42
    assert status["running"] is False
    assert status["paused"] is False
    assert status["jobs_processed"] == 0
    assert status["jobs_succeeded"] == 0
    assert status["jobs_failed"] == 0
    assert status["last_run"] is None

    # Start and check status again
    worker.start()
    time.sleep(0.5)
    status = worker.get_status()
    assert status["running"] is True

    worker.stop(timeout=5)
