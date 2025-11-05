# Phase 4: Async Embedding Queue

## Goals
- Decouple embedding generation from HTTP request path for non-blocking writes
- Implement background worker pool to process embedding jobs asynchronously
- Use database `embedding_status` field as persistent queue (no external dependencies)
- Provide observability into queue depth, worker health, and job status
- Ensure idempotent embedding operations to handle retries safely
- Coordinate queue with bulk import operations to prevent conflicts

## Success Criteria
- Write operations return immediately without waiting for embedding generation
- Background workers reliably process pending embeddings and update FAISS index
- Queue automatically recovers on API restart (requeue pending jobs)
- Job deduplication prevents double-processing of the same entity
- Admin endpoints provide visibility into queue state and worker health
- Bulk import operations can safely drain queue and pause workers
- System gracefully handles worker failures (automatic restart or retry)
- Performance: Write latency <100ms p95 (vs current ~2-5s with inline embedding)

## Prerequisites
- Phase 1 completed: API server with CRUD endpoints
- Phase 2 completed: MCP clients using HTTP API
- Phase 3 completed: Thread-safe FAISS with proper locking
- Understanding of current embedding service (src/embedding/service.py)
- Understanding of `embedding_status` field usage ('pending', 'embedded', 'failed')

## Detailed Design

### Core Architecture

**State-Based Queue Pattern**:
- Use existing `embedding_status` column as queue state indicator
- No external queue system (Redis, RabbitMQ) needed for v1
- Database is source of truth for job state
- Workers poll database for `embedding_status='pending'`

**Workflow**:
1. User creates/updates entry via API
2. API writes to database with `embedding_status='pending'`
3. API returns immediately (fast response)
4. Background worker picks up pending entry
5. Worker generates embedding and updates FAISS
6. Worker sets `embedding_status='embedded'` on success or `'failed'` on error
7. User can query entry status via API

### Components to Add

#### 1. Background Worker Infrastructure

**File**: `src/background/worker.py` (new)

```python
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
        """Stop worker thread gracefully."""
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
        """Get worker status for monitoring."""
        return {
            "worker_id": self.worker_id,
            "running": self.running,
            "paused": self.paused_event.is_set(),
            "jobs_processed": self.jobs_processed,
            "jobs_succeeded": self.jobs_succeeded,
            "jobs_failed": self.jobs_failed,
            "last_run": self.last_run,
        }
```

#### 2. Worker Pool Manager

**File**: `src/background/pool.py` (new)

```python
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
        """Stop all workers gracefully."""
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
        """Get status of all workers."""
        return {
            "num_workers": self.num_workers,
            "workers": [w.get_status() for w in self.workers],
            "total_jobs_processed": sum(w.jobs_processed for w in self.workers),
            "total_jobs_succeeded": sum(w.jobs_succeeded for w in self.workers),
            "total_jobs_failed": sum(w.jobs_failed for w in self.workers),
        }

    def get_queue_depth(self) -> dict:
        """Get current queue depth from database."""
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
```

#### 3. Startup Requeue Logic

**File**: `src/background/startup.py` (new)

```python
"""Startup logic for embedding queue."""
import logging

logger = logging.getLogger(__name__)

def requeue_pending_embeddings(session):
    """
    Requeue any entries stuck in 'pending' state on startup.

    This handles crash recovery: entries that were pending when the API
    shut down need to be reprocessed.

    Note: Since we use embedding_status='pending' as queue state, this
    is essentially a no-op (entries are already pending). But we log
    the count for visibility.
    """
    from src.storage.schema import Experience, CategoryManual

    pending_exp = session.query(Experience).filter(
        Experience.embedding_status == 'pending'
    ).count()

    pending_man = session.query(CategoryManual).filter(
        CategoryManual.embedding_status == 'pending'
    ).count()

    total = pending_exp + pending_man

    if total > 0:
        logger.info(
            f"Found {total} pending embeddings on startup "
            f"({pending_exp} experiences, {pending_man} manuals). "
            "Workers will process them."
        )
    else:
        logger.info("No pending embeddings on startup")

    return total
```

#### 4. Admin API Endpoints

**File**: `src/api/routers/admin.py` (extend)

Add queue management endpoints:

```python
from fastapi import APIRouter, Depends, HTTPException

router = APIRouter(prefix="/admin", tags=["admin"])

@router.get("/queue/status")
def get_queue_status():
    """
    Get queue status: pending jobs, failed jobs, worker health.
    """
    from src.api_server import worker_pool

    if not worker_pool:
        raise HTTPException(status_code=503, detail="Worker pool not initialized")

    worker_status = worker_pool.get_status()
    queue_depth = worker_pool.get_queue_depth()

    return {
        "queue": queue_depth,
        "workers": worker_status,
    }

@router.post("/queue/pause")
def pause_queue():
    """
    Pause all workers.

    Use this before bulk imports or maintenance operations.
    """
    from src.api_server import worker_pool

    if not worker_pool:
        raise HTTPException(status_code=503, detail="Worker pool not initialized")

    worker_pool.pause_all()
    return {"status": "paused"}

@router.post("/queue/resume")
def resume_queue():
    """Resume all workers."""
    from src.api_server import worker_pool

    if not worker_pool:
        raise HTTPException(status_code=503, detail="Worker pool not initialized")

    worker_pool.resume_all()
    return {"status": "resumed"}

@router.post("/queue/retry-failed")
def retry_failed():
    """
    Retry all failed embeddings by resetting status to 'pending'.
    """
    from src.api_server import db
    from src.storage.schema import Experience, CategoryManual

    with db.session_scope() as session:
        # Reset experiences
        exp_count = session.query(Experience).filter(
            Experience.embedding_status == 'failed'
        ).update({"embedding_status": "pending"})

        # Reset manuals
        man_count = session.query(CategoryManual).filter(
            CategoryManual.embedding_status == 'failed'
        ).update({"embedding_status": "pending"})

        session.commit()

    return {
        "retried": {
            "experiences": exp_count,
            "manuals": man_count,
            "total": exp_count + man_count,
        }
    }

@router.post("/queue/drain")
def drain_queue(timeout: int = 300):
    """
    Wait for queue to be empty (all pending jobs processed).

    Args:
        timeout: Maximum seconds to wait (default 300 = 5 minutes)

    Returns:
        Status after drain attempt
    """
    import time
    from src.api_server import worker_pool

    if not worker_pool:
        raise HTTPException(status_code=503, detail="Worker pool not initialized")

    start_time = time.time()
    while time.time() - start_time < timeout:
        depth = worker_pool.get_queue_depth()
        pending = depth["pending"]["total"]

        if pending == 0:
            return {
                "status": "drained",
                "elapsed": time.time() - start_time,
            }

        # Wait a bit before checking again
        time.sleep(5)

    # Timeout reached
    depth = worker_pool.get_queue_depth()
    return {
        "status": "timeout",
        "elapsed": timeout,
        "remaining": depth["pending"]["total"],
    }
```

#### 5. Integration with API Server

**File**: `src/api_server.py` (modify)

Add worker pool initialization:

```python
from contextlib import asynccontextmanager
from src.background.pool import WorkerPool
from src.background.startup import requeue_pending_embeddings

# Global worker pool
worker_pool: Optional[WorkerPool] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global config, db, search_service, embedding_service, worker_pool

    # ... existing initialization (config, db, search_service, embedding_service)

    # Initialize worker pool
    num_workers = config.num_embedding_workers  # e.g., 2
    worker_pool = WorkerPool(
        num_workers=num_workers,
        session_factory=db.get_session,
        embedding_service=embedding_service,
        poll_interval=config.worker_poll_interval,  # e.g., 5
        batch_size=config.worker_batch_size,  # e.g., 10
    )

    # Requeue pending embeddings from crash recovery
    with db.session_scope() as session:
        requeue_pending_embeddings(session)

    # Start workers
    worker_pool.start_all()
    logger.info("Embedding worker pool started")

    yield

    # Shutdown: stop workers gracefully
    logger.info("Shutting down worker pool")
    worker_pool.stop_all(timeout=30)
    logger.info("Worker pool stopped")

    # ... existing cleanup
```

#### 6. Remove Inline Embedding from Write Endpoints

**File**: `src/api/routers/entries.py` (modify)

Change write/update endpoints to NOT generate embeddings inline:

```python
@router.post("/write")
def write_entry(request: WriteEntryRequest, session = Depends(get_db_session)):
    """
    Create new entry.

    Note: Embedding generation is asynchronous. Entry is created with
    embedding_status='pending', and background workers will process it.
    """
    # ... create entity with embedding_status='pending'

    # DO NOT call embedding_service here (Phase 4 change)
    # Workers will pick up pending entries

    return {"entry": entry_dict, "embedding_status": "pending"}
```

**Important**: Remove or make optional the inline embedding logic that currently exists in `src/mcp/handlers_entries.py`.

### Idempotency Guarantees

**Problem**: If a worker crashes mid-processing, the same entry might be processed twice.

**Solution**: Use `upsert` pattern in EmbeddingService (already implemented):
- Check if embedding already exists before generating
- Use database constraints to prevent duplicate embeddings
- FAISS `update()` uses delete-then-add (safe for double-processing)

**Additional Safety**: Add timestamp tracking to detect stale jobs:

```python
# In schema.py (optional enhancement)
class Experience(Base):
    # ... existing fields
    embedding_started_at: Optional[datetime] = Column(DateTime, nullable=True)

# In worker
def _process_batch(self):
    # Mark as in-progress
    exp.embedding_started_at = utc_now()
    session.commit()

    # Process...

    # Clear timestamp on completion
    exp.embedding_started_at = None
    session.commit()

# Requeue stale jobs (optional admin endpoint)
@router.post("/queue/requeue-stale")
def requeue_stale(threshold_minutes: int = 30):
    """Requeue jobs stuck in progress for >threshold_minutes."""
    cutoff = utc_now() - timedelta(minutes=threshold_minutes)
    # Find entries with embedding_started_at < cutoff
    # Reset to pending
```

### Bulk Import Coordination

**Problem**: Bulk imports (Google Sheets) need to:
1. Pause queue to prevent concurrent processing
2. Clear database and FAISS
3. Import data with `embedding_status='pending'`
4. Resume queue to process embeddings

**Critical Requirements**:
- **Downtime Required**: Import must run during maintenance window when API can be paused/drained
- **Embedding Status Reset**: Import script **MUST** explicitly set `embedding_status='pending'` for ALL imported rows, ignoring any incoming values from Google Sheets (already implemented in scripts/import.py:363 and :386)
- **API Coordination**: If API is running, import must call pause → drain → import → resume sequence

**Solution**: Update `scripts/import.py`:

```python
# In scripts/import.py (modify)
import httpx

def main():
    # ... existing setup

    # Check if API is running
    api_url = os.getenv("CHL_API_BASE_URL", "http://localhost:8000")
    api_running = False

    try:
        response = httpx.get(f"{api_url}/health", timeout=5)
        api_running = response.status_code == 200
    except Exception:
        logger.warning("API server not reachable, proceeding with direct import")

    if api_running:
        # Pause queue
        logger.info("Pausing API queue for import")
        httpx.post(f"{api_url}/admin/queue/pause")

        # Wait for drain
        logger.info("Draining queue before import")
        drain_response = httpx.post(f"{api_url}/admin/queue/drain", timeout=600)
        if drain_response.json().get("status") != "drained":
            logger.warning("Queue did not drain completely")

    # ... existing import logic
    # Note: Existing code already sets embedding_status='pending' explicitly
    # on all imported rows (lines 363, 386), ignoring any incoming values.
    # This ensures all imported data will be processed by the queue.

    if api_running:
        # Resume queue
        logger.info("Resuming API queue")
        httpx.post(f"{api_url}/admin/queue/resume")
```

### Configuration

Add to `src/config.py`:

```python
class Config:
    # ... existing fields

    # Worker pool configuration
    num_embedding_workers: int = 2
    worker_poll_interval: int = 5  # seconds
    worker_batch_size: int = 10

    # Queue behavior
    queue_retry_failed_on_startup: bool = False
```

Environment variables:
```bash
CHL_NUM_EMBEDDING_WORKERS=2
CHL_WORKER_POLL_INTERVAL=5
CHL_WORKER_BATCH_SIZE=10
CHL_QUEUE_RETRY_FAILED_ON_STARTUP=0
```

## Implementation Plan

### Step 1: Create Worker Infrastructure
1. Create `src/background/` directory
2. Implement `EmbeddingWorker` class
3. Implement `WorkerPool` class
4. Add unit tests for worker behavior

### Step 2: Implement Startup Requeue
1. Create `startup.py` with requeue logic
2. Test crash recovery scenario
3. Add logging for visibility

### Step 3: Add Admin Endpoints
1. Implement `/admin/queue/status`
2. Implement `/admin/queue/pause` and `/resume`
3. Implement `/admin/queue/drain`
4. Implement `/admin/queue/retry-failed`
5. Add authentication (basic auth or API key)

### Step 4: Integrate with API Server
1. Add worker pool initialization to `api_server.py`
2. Update lifespan to start/stop workers
3. Remove inline embedding from write endpoints
4. Test async workflow (write → background process)

### Step 5: Update Bulk Import Script
1. Add API pause/drain/resume logic
2. Test import with API running
3. Test import with API stopped (fallback)

### Step 6: Add Observability
1. Add queue metrics to `/metrics` endpoint
2. Add worker health to `/health` endpoint
3. Add structured logging for job processing
4. Create monitoring dashboard (optional)

### Step 7: Testing
1. Unit tests for workers, pool, startup
2. Integration tests for async workflow
3. Test crash recovery (kill workers mid-job)
4. Test bulk import coordination
5. Load testing with queue enabled
6. Test idempotency (double-processing)

### Step 8: Documentation
1. Update API documentation
2. Document admin endpoints
3. Document monitoring and troubleshooting
4. Update deployment guide

## Testing Strategy

### Unit Tests
- `test_embedding_worker.py` - Worker logic, error handling
- `test_worker_pool.py` - Pool management
- `test_startup.py` - Requeue logic

### Integration Tests
- `test_async_embedding_workflow.py` - Write → background process → verify embedded
- `test_worker_crash_recovery.py` - Kill worker, verify restart
- `test_bulk_import_coordination.py` - Import with queue pause/resume

### Load Tests
- 1000 writes in rapid succession, verify all get embedded
- Measure latency improvement (should be <100ms vs 2-5s inline)

## Acceptance Criteria

- [ ] Write operations complete in <100ms p95 (async)
- [ ] Background workers process all pending embeddings
- [ ] Workers automatically restart on crash
- [ ] Queue depth visible via `/admin/queue/status`
- [ ] Pause/resume endpoints work correctly
- [ ] Drain endpoint waits for queue to empty
- [ ] Bulk import coordinates with queue (pause/drain/resume)
- [ ] Failed jobs can be retried via `/admin/queue/retry-failed`
- [ ] System recovers on restart (requeues pending)
- [ ] No duplicate embeddings (idempotency verified)
- [ ] All tests pass with >80% coverage
- [ ] Performance: 1000 async writes complete in <2 minutes

## Operational Considerations

### Deployment
1. Deploy API with workers disabled first (test endpoints)
2. Enable workers with low count (1-2)
3. Monitor queue depth and worker health
4. Scale up workers if queue grows

### Monitoring
- Alert if queue depth >100 for >10 minutes
- Alert if worker hasn't run in >5 minutes
- Alert if failed job count >50
- Monitor embedding latency per job

### Troubleshooting

**Queue backing up**:
- Check worker health: `curl /admin/queue/status`
- Check for errors in worker logs
- Increase num_workers or batch_size

**Workers not processing**:
- Check if paused: `/admin/queue/status`
- Resume if needed: `POST /admin/queue/resume`
- Check database connectivity

**Failed jobs accumulating**:
- Check error logs for root cause
- Retry failed: `POST /admin/queue/retry-failed`
- Fix underlying issue (embedding model, FAISS)

## Open Questions

- [ ] Should we add priority queue (high/low priority entries)? (Recommendation: No for Phase 4)
- [ ] Should we add job timeout (kill stuck jobs)? (Recommendation: Yes, add stale job detection)
- [ ] Should we add distributed locking for multi-process deployment? (Recommendation: No, single process for now)
- [ ] Should we support external queue (Redis/RabbitMQ)? (Recommendation: No, state-based queue is sufficient)

## Dependencies from Other Phases

**Depends on Phase 1**:
- API server infrastructure

**Depends on Phase 2**:
- MCP clients using HTTP API

**Depends on Phase 3**:
- Thread-safe FAISS locking (workers need to acquire lock)

## Notes
- State-based queue (using database) is simpler than message queue for v1
- Workers are daemon threads (will exit when main process exits)
- Pause/resume is critical for coordinating with bulk imports
- Idempotency via upsert pattern prevents duplicate embeddings
- Admin endpoints should be protected (add authentication)
- Consider adding metrics export (Prometheus) for production monitoring
