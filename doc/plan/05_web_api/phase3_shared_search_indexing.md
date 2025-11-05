# Phase 3: Shared Search & Indexing

## Goals
- Consolidate FAISS index management into the API server to ensure all clients see consistent search results
- Implement robust concurrency control for FAISS operations
- Establish reliable index persistence and recovery mechanisms
- Verify that data written by one client is immediately searchable by all others
- Prepare foundation for async embedding queue (Phase 4)

## Success Criteria
- Single shared FAISS index serves all MCP clients
- Concurrent searches from multiple clients work correctly
- Index updates (add/update/delete) are thread-safe with proper locking
- Index persistence strategy prevents data loss during crashes
- Tombstone-based lazy deletion triggers automatic rebuild at 10% threshold
- Multi-client integration tests verify cross-client consistency
- Search results are identical to Phase 2 (behavioral compatibility)

## Prerequisites
- Phase 1 completed: API server is stable
- Phase 2 completed: MCP clients successfully use HTTP API
- Understanding of current FAISS implementation (src/search/faiss_index.py)
- Understanding of current search service architecture (src/search/service.py)

## Detailed Design

### Core Problem
**Current State**: Each MCP client process has its own FAISS index instance, leading to:
- Divergent state (writes in one client don't appear in another's searches)
- Race conditions during concurrent writes
- Wasted memory (duplicate index loading)
- Inconsistent search results

**Target State**: Single FAISS index in API server, shared by all clients via HTTP.

### Components to Enhance

#### 1. FAISS Locking Mechanism

**Issue**: `FAISSIndexManager` is documented as not thread-safe (src/search/faiss_index.py:26).

**Solution**: Add explicit locking at the SearchService level.

**File**: `src/search/service.py` (modify)

Add thread-safe wrapper:
```python
import threading
from typing import Optional

class ThreadSafeFAISSManager:
    """
    Thread-safe wrapper around FAISSIndexManager.

    Uses RLock (reentrant lock) to allow same thread to acquire multiple times.
    This is necessary because some operations (e.g., update) internally call
    delete + add, both of which need the lock.
    """

    def __init__(self, faiss_manager):
        self._manager = faiss_manager
        self._lock = threading.RLock()

    def search(self, *args, **kwargs):
        """Thread-safe search."""
        with self._lock:
            return self._manager.search(*args, **kwargs)

    def add(self, *args, **kwargs):
        """Thread-safe add with automatic save."""
        with self._lock:
            result = self._manager.add(*args, **kwargs)
            self._save_safely()
            return result

    def update(self, *args, **kwargs):
        """Thread-safe update with automatic save."""
        with self._lock:
            result = self._manager.update(*args, **kwargs)
            self._save_safely()
            return result

    def delete(self, *args, **kwargs):
        """Thread-safe delete with automatic save."""
        with self._lock:
            result = self._manager.delete(*args, **kwargs)
            self._save_safely()

            # Check if rebuild needed (tombstone ratio > 10%)
            if self._manager.needs_rebuild():
                logger.info("Tombstone ratio exceeds 10%, triggering rebuild")
                self._rebuild_index()

            return result

    def _save_safely(self):
        """
        Save index with atomic rename and backup.

        Strategy:
        1. Write to temp file
        2. Backup existing index
        3. Atomic rename temp → main
        """
        import shutil
        from pathlib import Path

        index_path = self._manager.index_path
        backup_path = index_path.with_suffix('.index.backup')
        temp_path = index_path.with_suffix('.index.tmp')

        try:
            # Write to temp file
            self._manager.faiss.write_index(self._manager.index, str(temp_path))

            # Backup existing index
            if index_path.exists():
                shutil.copy2(index_path, backup_path)

            # Atomic rename
            temp_path.rename(index_path)

            # Save metadata
            self._manager._save_metadata()

            logger.debug("FAISS index saved successfully")

        except Exception as e:
            logger.error(f"Failed to save FAISS index: {e}")
            # Clean up temp file
            if temp_path.exists():
                temp_path.unlink()
            raise

    def _rebuild_index(self):
        """
        Rebuild FAISS index from scratch.

        Process:
        1. Query all non-deleted embeddings from database
        2. Create new FAISS index
        3. Batch add all embeddings
        4. Clear tombstones in faiss_metadata
        5. Save new index atomically
        """
        from src.storage.schema import Embedding, FAISSMetadata

        logger.info("Starting FAISS index rebuild")
        session = self._manager.session

        try:
            # Get all embeddings
            embeddings = session.query(Embedding).filter(
                Embedding.model_name == self._manager.model_name
            ).all()

            # Get metadata to determine entity types
            metadata_map = {}
            for meta in session.query(FAISSMetadata).filter(
                FAISSMetadata.deleted == False
            ).all():
                metadata_map[meta.entity_id] = meta.entity_type

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
                logger.warning("No embeddings found for rebuild")
                return

            import numpy as np
            embeddings_array = np.vstack(embedding_vectors).astype(np.float32)

            # Create new index and reset metadata
            self._manager._create_new_index(reset_metadata=True)

            # Batch add
            self._manager.add(entity_ids, entity_types, embeddings_array)

            # Save
            self._save_safely()

            logger.info(
                f"FAISS index rebuild complete: {len(entity_ids)} vectors"
            )

        except Exception as e:
            logger.error(f"FAISS rebuild failed: {e}")
            session.rollback()
            raise

    @property
    def is_available(self):
        """Check if FAISS is available."""
        return self._manager.is_available

    def __getattr__(self, name):
        """Delegate other attributes to underlying manager."""
        return getattr(self._manager, name)
```

**Integration**: Modify SearchService initialization to wrap FAISS manager:
```python
class SearchService:
    def __init__(self, ...):
        # ... existing initialization
        if faiss_index_manager:
            self.faiss_index_manager = ThreadSafeFAISSManager(faiss_index_manager)
        else:
            self.faiss_index_manager = None
```

#### 2. Index Persistence Strategy

**Phase 1 Implementation**: Save after every write (simple but potentially slow).

**Phase 3 Enhancement**: Add configurable persistence policy.

**File**: `src/config.py` (modify)

Add configuration:
```python
class Config:
    # ... existing fields

    # FAISS persistence configuration
    faiss_save_policy: str = "immediate"  # "immediate", "periodic", "manual"
    faiss_save_interval: int = 300  # seconds (for periodic mode)
    faiss_rebuild_threshold: float = 0.10  # tombstone ratio
```

**File**: `src/search/service.py` (modify)

Implement periodic save:
```python
class PeriodicSaver:
    """
    Background thread that periodically saves FAISS index.

    Only used when save_policy = "periodic".
    """

    def __init__(self, faiss_manager, interval: int):
        self.faiss_manager = faiss_manager
        self.interval = interval
        self.dirty = False
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        """Start periodic save thread."""
        self.thread.start()
        logger.info(f"Periodic FAISS saver started (interval={self.interval}s)")

    def stop(self):
        """Stop periodic save thread."""
        self.stop_event.set()
        self.thread.join()

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

# Modify ThreadSafeFAISSManager to use periodic saver
class ThreadSafeFAISSManager:
    def __init__(self, faiss_manager, save_policy="immediate", save_interval=300):
        self._manager = faiss_manager
        self._lock = threading.RLock()
        self._save_policy = save_policy
        self._periodic_saver = None

        if save_policy == "periodic":
            self._periodic_saver = PeriodicSaver(self, save_interval)
            self._periodic_saver.start()

    def add(self, *args, **kwargs):
        with self._lock:
            result = self._manager.add(*args, **kwargs)

            if self._save_policy == "immediate":
                self._save_safely()
            elif self._save_policy == "periodic":
                self._periodic_saver.mark_dirty()

            return result
```

#### 3. Index Recovery Procedures

**Startup Recovery**:
```python
def initialize_faiss_with_recovery(config, session):
    """
    Initialize FAISS index with automatic recovery.

    Recovery steps:
    1. Try to load existing index
    2. If load fails, try backup
    3. If backup fails, rebuild from database
    4. If rebuild fails, disable FAISS (fallback to text search)
    """
    from src.search.faiss_index import FAISSIndexManager, FAISSIndexError

    faiss_manager = FAISSIndexManager(
        index_dir=str(config.faiss_index_dir),
        model_name=config.embedding_model_repo,
        dimension=config.embedding_dimension,
        session=session
    )

    try:
        # Try normal load
        _ = faiss_manager.index  # Triggers lazy load
        logger.info("FAISS index loaded successfully")
        return faiss_manager

    except FAISSIndexError as e:
        logger.warning(f"Failed to load FAISS index: {e}")

        # Try backup
        backup_path = faiss_manager.index_path.with_suffix('.index.backup')
        if backup_path.exists():
            try:
                logger.info("Attempting to restore from backup")
                import shutil
                shutil.copy2(backup_path, faiss_manager.index_path)
                _ = faiss_manager.index
                logger.info("FAISS index restored from backup")
                return faiss_manager
            except Exception as e2:
                logger.error(f"Backup restore failed: {e2}")

        # Rebuild from database
        try:
            logger.info("Attempting to rebuild index from database")
            faiss_manager._create_new_index(reset_metadata=True)

            # Use rebuild logic from ThreadSafeFAISSManager
            from src.storage.schema import Embedding
            embeddings = session.query(Embedding).filter(
                Embedding.model_name == config.embedding_model_repo
            ).all()

            if embeddings:
                # Rebuild logic here (see _rebuild_index above)
                logger.info("FAISS index rebuilt successfully")
                return faiss_manager
            else:
                logger.warning("No embeddings found, starting with empty index")
                return faiss_manager

        except Exception as e3:
            logger.error(f"Index rebuild failed: {e3}")
            logger.warning("FAISS will be unavailable, falling back to text search")
            return None
```

#### 4. Multi-Client Consistency Verification

**Add validation endpoint**:

**File**: `src/api/routers/admin.py` (new)

```python
from fastapi import APIRouter, Depends
from src.api.dependencies import get_search_service

router = APIRouter(prefix="/admin", tags=["admin"])

@router.get("/index/status")
def get_index_status(search_service = Depends(get_search_service)):
    """
    Get FAISS index status for debugging.

    Returns:
    - Index size (number of vectors)
    - Tombstone ratio
    - Last save time
    - Rebuild needed flag
    """
    if not search_service.faiss_index_manager:
        return {"status": "unavailable", "reason": "FAISS not loaded"}

    manager = search_service.faiss_index_manager._manager

    return {
        "status": "available",
        "index_size": manager.index.ntotal,
        "tombstone_ratio": manager.get_tombstone_ratio(),
        "needs_rebuild": manager.needs_rebuild(),
        "model_name": manager.model_name,
        "dimension": manager.dimension,
    }

@router.post("/index/rebuild")
def trigger_rebuild(search_service = Depends(get_search_service)):
    """
    Manually trigger FAISS index rebuild.

    Warning: This is a blocking operation and may take several seconds.
    """
    if not search_service.faiss_index_manager:
        return {"error": "FAISS not available"}

    try:
        search_service.faiss_index_manager._rebuild_index()
        return {"status": "success", "message": "Index rebuilt"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
```

### Concurrency Testing Approach

**Goal**: Verify that multiple concurrent clients can safely read/write.

**Test Scenarios**:
1. **Concurrent reads**: 10 clients search simultaneously
2. **Concurrent writes**: 5 clients write different entries simultaneously
3. **Mixed read/write**: 10 clients (5 read, 5 write) operate concurrently
4. **Write-then-read**: Client A writes, Client B immediately searches for it
5. **Stress test**: 50 clients with mixed operations for 60 seconds

**Implementation**:

**File**: `tests/integration/test_concurrent_faiss.py` (new)

```python
import threading
import time
from fastapi.testclient import TestClient
from src.api_server import app

def test_concurrent_searches():
    """Test multiple clients searching simultaneously."""
    client = TestClient(app)
    results = []
    errors = []

    def search_worker(worker_id):
        try:
            response = client.post("/api/v1/entries/read", json={
                "entity_type": "experience",
                "category_code": "PGS",
                "query": "test query"
            })
            response.raise_for_status()
            results.append(response.json())
        except Exception as e:
            errors.append((worker_id, str(e)))

    # Launch 10 concurrent searchers
    threads = [
        threading.Thread(target=search_worker, args=(i,))
        for i in range(10)
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Verify no errors
    assert len(errors) == 0, f"Errors occurred: {errors}"

    # Verify all searches returned same results (consistency)
    assert len(results) == 10
    # Compare result hashes to ensure consistency
    result_hashes = [hash(str(r)) for r in results]
    assert len(set(result_hashes)) == 1, "Inconsistent search results"

def test_write_then_read():
    """Test that writes are immediately visible to other clients."""
    client = TestClient(app)

    # Client A writes
    write_response = client.post("/api/v1/entries/write", json={
        "entity_type": "experience",
        "category_code": "TST",
        "data": {
            "section": "useful",
            "title": "Test concurrent write",
            "playbook": "This should be immediately searchable"
        }
    })
    assert write_response.status_code == 200
    entry_id = write_response.json()["entry"]["id"]

    # Small delay to ensure embedding completes (Phase 3 still synchronous)
    time.sleep(1)

    # Client B searches
    search_response = client.post("/api/v1/entries/read", json={
        "entity_type": "experience",
        "category_code": "TST",
        "query": "concurrent write"
    })
    assert search_response.status_code == 200

    # Verify Client B sees Client A's write
    entries = search_response.json()["entries"]
    assert any(e["id"] == entry_id for e in entries), \
        "Newly written entry not visible to other client"
```

## Implementation Plan

### Step 1: Implement FAISS Locking
1. Create `ThreadSafeFAISSManager` wrapper class
2. Add RLock for all FAISS operations
3. Implement atomic save with backup
4. Add unit tests for lock behavior

### Step 2: Implement Index Persistence
1. Add configuration options (immediate/periodic/manual)
2. Implement periodic saver background thread
3. Test save policies under load

### Step 3: Implement Index Recovery
1. Create recovery logic (backup → rebuild → disable)
2. Add recovery tests (corrupted index, missing index)
3. Integrate into API server startup

### Step 4: Implement Rebuild Logic
1. Add automatic rebuild on 10% tombstone ratio
2. Add manual rebuild endpoint
3. Test rebuild correctness

### Step 5: Add Admin Endpoints
1. Create `/admin/index/status` endpoint
2. Create `/admin/index/rebuild` endpoint
3. Add authentication (basic auth or API key)

### Step 6: Concurrency Testing
1. Write concurrent read tests
2. Write concurrent write tests
3. Write mixed workload tests
4. Write write-then-read consistency tests
5. Run stress tests with load generators

### Step 7: Integration with Existing Code
1. Update API server to use ThreadSafeFAISSManager
2. Update all entry endpoints to use locked FAISS
3. Verify backward compatibility

### Step 8: Documentation
1. Document persistence policies
2. Document recovery procedures
3. Add troubleshooting guide for index corruption

## Testing Strategy

### Unit Tests
- `test_thread_safe_faiss_manager.py` - Lock behavior, save logic
- `test_periodic_saver.py` - Background thread behavior
- `test_index_recovery.py` - Recovery logic

### Integration Tests
- `test_concurrent_faiss.py` - Multi-client scenarios
- `test_index_persistence.py` - Save/load cycles
- `test_index_rebuild.py` - Rebuild correctness

### Stress Tests
Use `locust` to simulate heavy concurrent load:
```python
class ConcurrentUser(HttpUser):
    @task(3)
    def search(self):
        self.client.post("/api/v1/entries/read", json={...})

    @task(1)
    def write(self):
        self.client.post("/api/v1/entries/write", json={...})
```

Run: `locust -f stress_test.py --users 50 --spawn-rate 5 --run-time 5m`

## Acceptance Criteria

- [ ] FAISS operations are thread-safe (verified with concurrent tests)
- [ ] Multiple clients can search simultaneously without errors
- [ ] Writes from one client are immediately visible to others
- [ ] Index saves successfully after every write (or periodically)
- [ ] Corrupted index recovers from backup automatically
- [ ] Index rebuilds when tombstone ratio exceeds 10%
- [ ] Manual rebuild endpoint works correctly
- [ ] Admin status endpoint shows accurate index state
- [ ] Stress test (50 users, 5 minutes) completes without errors
- [ ] No data loss during index saves or crashes
- [ ] Performance degradation <10% compared to Phase 2

## Operational Considerations

### Configuration
```bash
# FAISS persistence
CHL_FAISS_SAVE_POLICY=immediate     # immediate, periodic, manual
CHL_FAISS_SAVE_INTERVAL=300         # seconds (for periodic)
CHL_FAISS_REBUILD_THRESHOLD=0.10    # tombstone ratio
```

### Monitoring
- Monitor tombstone ratio (alert if >8%)
- Monitor index save failures
- Monitor rebuild operations (should be rare)
- Monitor lock contention (log if acquire takes >1s)

### Manual Recovery Steps
**If index is corrupted**:
1. Stop API server
2. Check backup: `ls data/faiss_index/*.backup`
3. Restore: `cp data/faiss_index/unified_*.backup data/faiss_index/unified_*.index`
4. Start API server (will load backup)

**If backup also corrupted**:
1. Stop API server
2. Run: `python scripts/rebuild_index.py`
3. Start API server

**If rebuild fails**:
1. API will start with FAISS disabled
2. Text search fallback will be used
3. Investigate database/embedding issues
4. Fix and retry rebuild

## Open Questions

- [ ] Should we add index versioning for rollback? (Recommendation: No for Phase 3, add in Phase 4 if needed)
- [ ] Should rebuild be automatic or require manual trigger? (Recommendation: Automatic at 10%, manual endpoint for emergency)
- [ ] Should we add index compaction (beyond rebuild)? (Recommendation: No, rebuild is sufficient)
- [ ] Should we support multiple index snapshots? (Recommendation: No, single backup is sufficient)

## Dependencies from Other Phases

**Depends on Phase 1**:
- API server infrastructure

**Depends on Phase 2**:
- MCP clients using HTTP API

**Consumed by Phase 4**:
- Locking mechanism must support queue workers
- Rebuild logic must handle async embedding status
- Save logic must coordinate with background jobs

## Notes
- RLock (reentrant) is essential because `update()` calls `delete()` + `add()`
- Periodic save reduces I/O but increases risk of data loss on crash
- Tombstone deletion is lazy (rebuild at 10%) to avoid expensive operations
- Recovery logic prioritizes availability over perfection (fallback to text search)
- Admin endpoints should be protected (add basic auth or API key)
