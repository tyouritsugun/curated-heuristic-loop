# Phase 1: HTTP API Foundation

## Goals
- Stand up a working FastAPI service that provides HTTP access to existing CRUD and search capabilities
- Establish shared resource management (database, FAISS, embedding model) in a single process
- Implement health checks and basic observability
- Create a stable foundation for MCP integration (Phase 2) and async processing (Phase 4)

## Success Criteria
- API serves all existing MCP tool operations via HTTP endpoints
- Multiple concurrent requests can safely access the database and search index
- Health endpoint reports system status (database, FAISS, embedding model)
- All operations have identical behavior to current direct-database MCP implementation
- Basic metrics are exposed for monitoring

## Prerequisites
- Current codebase at working state with all existing functionality
- Understanding of current `src/storage/`, `src/search/`, and `src/embedding/` implementations
- FastAPI and SQLAlchemy experience

## Detailed Design

### Components to Add

#### 1. API Server Entrypoint
**File**: `src/api_server.py` (new)

Main FastAPI application entrypoint. Responsibilities:
- Initialize FastAPI app with metadata, CORS, exception handlers
- Load configuration from environment
- Initialize database, FAISS index manager, embedding client (singleton pattern)
- Register routers
- Provide startup/shutdown lifecycle hooks
- Configure structured logging

Structure:
```python
from fastapi import FastAPI
from contextlib import asynccontextmanager

# Global singletons (initialized on startup)
config = None
db = None
search_service = None
embedding_service = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize global resources
    global config, db, search_service, embedding_service
    # ... initialization logic

    yield

    # Shutdown: Cleanup resources
    # ... cleanup logic

app = FastAPI(lifespan=lifespan)

# Register routers
app.include_router(categories_router)
app.include_router(entries_router)
app.include_router(search_router)
app.include_router(health_router)
```

#### 2. API Module Structure
**Directory**: `src/api/` (new)

```
src/api/
├── app.py                    # FastAPI app factory
├── dependencies.py           # Dependency injection (get_db_session, get_search_service)
├── models.py                 # Pydantic request/response models
├── routers/
│   ├── categories.py        # Category endpoints
│   ├── entries.py           # Experience/Manual CRUD endpoints
│   ├── search.py            # Search endpoint
│   └── health.py            # Health check endpoints
└── exceptions.py            # Custom exception handlers
```

**Note**: We avoid `__init__.py` files to keep modules simple and explicit. Each module is imported directly (e.g., `from src.api.routers.categories import router`).

#### 3. Pydantic Models
**File**: `src/api/models.py` (new)

Define request/response schemas for all endpoints. These should mirror the MCP tool schemas but use Pydantic for validation.

Key models:
```python
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class CategoryResponse(BaseModel):
    code: str
    name: str
    description: Optional[str] = None

class ListCategoriesResponse(BaseModel):
    categories: List[CategoryResponse]

class ReadEntriesRequest(BaseModel):
    entity_type: str  # 'experience' or 'manual'
    category_code: str
    query: Optional[str] = None
    ids: Optional[List[str]] = None
    limit: Optional[int] = None

class WriteEntryRequest(BaseModel):
    entity_type: str
    category_code: str
    data: Dict[str, Any]

class UpdateEntryRequest(BaseModel):
    entity_type: str
    category_code: str
    entry_id: str
    updates: Dict[str, Any]
    force_contextual: bool = False

class DeleteEntryRequest(BaseModel):
    entity_type: str
    category_code: str
    entry_id: str

class HealthResponse(BaseModel):
    status: str  # 'healthy', 'degraded', 'unhealthy'
    components: Dict[str, Dict[str, Any]]
    timestamp: str

class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    error_code: Optional[str] = None
```

#### 4. Dependency Injection
**File**: `src/api/dependencies.py` (new)

Provide FastAPI dependencies for shared resources with proper request-scoped lifecycle:

```python
from typing import Generator
from sqlalchemy.orm import Session
from fastapi import Depends

def get_db_session() -> Generator[Session, None, None]:
    """
    Provide request-scoped database session.

    Note: Uses scoped_session which is thread-local. FastAPI uses thread pools
    for sync endpoints, so each request gets its own session.
    """
    from src.api_server import db

    session = db.get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

def get_search_service():
    """Provide singleton SearchService instance."""
    from src.api_server import search_service
    return search_service

def get_embedding_service():
    """Provide singleton EmbeddingService instance."""
    from src.api_server import embedding_service
    return embedding_service

def get_config():
    """Provide singleton Config instance."""
    from src.api_server import config
    return config
```

#### 5. Routers

**File**: `src/api/routers/categories.py` (new)
```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from src.api.dependencies import get_db_session
from src.api.models import ListCategoriesResponse, CategoryResponse
from src.storage.repository import CategoryRepository

router = APIRouter(prefix="/api/v1/categories", tags=["categories"])

@router.get("/", response_model=ListCategoriesResponse)
def list_categories(session: Session = Depends(get_db_session)):
    """List all available category shelves."""
    repo = CategoryRepository(session)
    categories = repo.list_all()

    return ListCategoriesResponse(
        categories=[
            CategoryResponse(
                code=cat.code,
                name=cat.name,
                description=cat.description
            )
            for cat in categories
        ]
    )
```

**File**: `src/api/routers/entries.py` (new)

Implement:
- `POST /api/v1/entries/read` - Read entries by query or IDs
- `POST /api/v1/entries/write` - Create new entry
- `POST /api/v1/entries/update` - Update existing entry
- `DELETE /api/v1/entries/delete` - Delete entry

These endpoints should delegate to the existing MCP handler logic (from `src/mcp/handlers_entries.py`) or directly use repositories. For Phase 1, embeddings are still synchronous inline (async queue comes in Phase 4).

**File**: `src/api/routers/search.py` (new)
```python
from fastapi import APIRouter, Depends
from src.api.dependencies import get_search_service
from src.api.models import SearchRequest, SearchResponse

router = APIRouter(prefix="/api/v1/search", tags=["search"])

@router.post("/", response_model=SearchResponse)
def search(
    request: SearchRequest,
    search_service = Depends(get_search_service)
):
    """
    Search for entries using semantic or text search.

    Note: This is a simplified endpoint. Full search capability
    is integrated into read_entries endpoint.
    """
    # Implementation uses search_service.search()
    pass
```

**File**: `src/api/routers/health.py` (new)
```python
from fastapi import APIRouter, Depends
from src.api.dependencies import get_db_session, get_search_service, get_config
from src.api.models import HealthResponse

router = APIRouter(prefix="/health", tags=["health"])

@router.get("/", response_model=HealthResponse)
def health_check(
    config = Depends(get_config),
    session = Depends(get_db_session),
    search_service = Depends(get_search_service)
):
    """
    Health check endpoint reporting system status.

    Status levels:
    - healthy: All critical components operational
    - degraded: Non-critical components failing (e.g., FAISS unavailable, falling back to text search)
    - unhealthy: Critical components failing (database, embedding model)

    Returns 200 for healthy/degraded, 503 for unhealthy.
    """
    components = {}
    overall_status = "healthy"

    # Check database
    try:
        session.execute("SELECT 1")
        components["database"] = {"status": "healthy", "detail": "Connected"}
    except Exception as e:
        components["database"] = {"status": "unhealthy", "detail": str(e)}
        overall_status = "unhealthy"

    # Check FAISS index
    try:
        if search_service.faiss_index_manager.is_available:
            index_size = search_service.faiss_index_manager.index.ntotal
            components["faiss_index"] = {
                "status": "healthy",
                "detail": f"{index_size} vectors"
            }
        else:
            components["faiss_index"] = {
                "status": "degraded",
                "detail": "FAISS not available, using text search fallback"
            }
            if overall_status == "healthy":
                overall_status = "degraded"
    except Exception as e:
        components["faiss_index"] = {"status": "degraded", "detail": str(e)}
        if overall_status == "healthy":
            overall_status = "degraded"

    # Check embedding model
    try:
        if search_service.embedding_client:
            model_info = search_service.embedding_client.get_model_version()
            components["embedding_model"] = {
                "status": "healthy",
                "detail": f"Loaded: {model_info}"
            }
        else:
            components["embedding_model"] = {
                "status": "unhealthy",
                "detail": "Model not loaded"
            }
            overall_status = "unhealthy"
    except Exception as e:
        components["embedding_model"] = {"status": "unhealthy", "detail": str(e)}
        overall_status = "unhealthy"

    # Add timestamp
    from datetime import datetime, timezone
    timestamp = datetime.now(timezone.utc).isoformat()

    response = HealthResponse(
        status=overall_status,
        components=components,
        timestamp=timestamp
    )

    # Return 503 if unhealthy
    if overall_status == "unhealthy":
        from fastapi import Response
        return Response(
            content=response.json(),
            status_code=503,
            media_type="application/json"
        )

    return response
```

### Dependencies to Add

**File**: `pyproject.toml` (modify)

Add to `dependencies` section:
```toml
dependencies = [
    "fastmcp>=0.3.0",
    "sqlalchemy>=2.0.0",
    "numpy>=1.24.0,<2.0.0",
    "gspread>=5.0.0",
    "google-auth>=2.0.0",
    "google-auth-oauthlib>=1.0.0",
    "tqdm>=4.65.0",
    "pyyaml>=6.0",
    # NEW - Phase 1 additions
    "fastapi>=0.114.0",
    "uvicorn[standard]>=0.30.0",
    "httpx>=0.27.0",
    "python-multipart>=0.0.6",
    "pydantic>=2.6.0",
]
```

**Justifications**:
- `fastapi>=0.114.0` - Core API framework with async support, improved performance and type hints
- `uvicorn[standard]>=0.30.0` - ASGI server with performance optimizations (uvloop, httptools), latest stability fixes
- `httpx>=0.27.0` - Async HTTP client (needed in Phase 2 for MCP shim), improved connection pooling
- `python-multipart>=0.0.6` - Form data parsing (FastAPI dependency)
- `pydantic>=2.6.0` - Data validation (FastAPI dependency), improved validation performance

### Database Session Management Strategy

**Critical Issue Resolution**: The current `Database` class uses `scoped_session`, which provides **thread-local sessions**, not connection pooling.

**Solution for Phase 1**:
1. Keep `scoped_session` for thread-local session management (works with FastAPI's thread pool)
2. Use `get_db_session()` dependency for request-scoped sessions
3. Each request gets a fresh session, commits on success, rolls back on error
4. Sessions are closed in the `finally` block to prevent leaks

**Why this works**:
- FastAPI runs sync endpoint handlers in a thread pool
- Each thread gets its own scoped session
- Request-scoped dependencies ensure proper commit/rollback lifecycle
- No connection pooling needed for SQLite (single-writer model)

**SQLite Concurrency Handling**:
- Add `busy_timeout` pragma to avoid `SQLITE_BUSY` errors:
```python
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")  # 5 second timeout
    cursor.close()
```

### Thread-Safety for FAISS

**Critical Issue Resolution**: `FAISSIndexManager` is explicitly not thread-safe.

**Solution for Phase 1**:
1. FAISS index manager is initialized as a singleton in `api_server.py`
2. Add a **global lock** for all FAISS operations:

```python
# In src/api_server.py
import threading

faiss_lock = threading.RLock()

# Wrap FAISS operations in search_service
class ThreadSafeSearchService:
    def __init__(self, search_service, faiss_lock):
        self._search_service = search_service
        self._lock = faiss_lock

    def search(self, *args, **kwargs):
        # Read operations can be concurrent with RLock
        with self._lock:
            return self._search_service.search(*args, **kwargs)
```

**Phase 4 Note**: When async embedding queue is added, queue workers will also need to acquire `faiss_lock` before updating the index.

**Lock Granularity**:
- Use `threading.RLock` (reentrant lock) to allow same thread to acquire multiple times
- Lock scope: entire FAISS operation (search, add, update, delete)
- Performance impact: Minimal for read-heavy workload (typical case)
- Scalability: Sufficient for single-machine deployment with <100 req/sec

### FAISS Persistence Strategy

**Critical Issue Resolution**: Define when and how to save FAISS index.

**Phase 1 Strategy** (synchronous):
1. **Save after every write operation** (add/update/delete)
   - Simple, safe, ensures minimal data loss
   - Acceptable performance for low write rate (<10 writes/min)
   - Implemented in each endpoint that modifies FAISS

2. **Atomic save with backup**:
```python
def save_index_safely(faiss_manager):
    """Save index with atomic rename and backup."""
    import shutil
    from pathlib import Path

    index_path = faiss_manager.index_path
    backup_path = index_path.with_suffix('.index.backup')
    temp_path = index_path.with_suffix('.index.tmp')

    # Write to temp file
    faiss_manager.faiss.write_index(faiss_manager.index, str(temp_path))

    # Backup existing index
    if index_path.exists():
        shutil.copy2(index_path, backup_path)

    # Atomic rename
    temp_path.rename(index_path)
```

3. **Recovery procedure** (manual for Phase 1):
   - If index file is corrupted, restore from `.backup`
   - If both corrupted, run `scripts/rebuild_index.py`

**Phase 4 Enhancement**: Move to periodic saves + write-ahead log.

### Observability

#### Logging
**Use structured logging** with JSON output for easy parsing:

```python
# In src/api_server.py
import logging
import json
from datetime import datetime

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)

# Configure on startup
handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.root.addHandler(handler)
logging.root.setLevel(logging.INFO)
```

#### Metrics (Basic)
**File**: `src/api/metrics.py` (new)

Use in-memory counters for Phase 1 (no external dependencies):

```python
from collections import defaultdict
import time
import threading

class SimpleMetrics:
    """Thread-safe in-memory metrics collector."""

    def __init__(self):
        self._counters = defaultdict(int)
        self._histograms = defaultdict(list)
        self._lock = threading.Lock()

    def increment(self, name: str, value: int = 1):
        with self._lock:
            self._counters[name] += value

    def observe(self, name: str, value: float):
        with self._lock:
            self._histograms[name].append(value)

    def get_snapshot(self):
        with self._lock:
            return {
                "counters": dict(self._counters),
                "histograms": {
                    k: {
                        "count": len(v),
                        "sum": sum(v),
                        "avg": sum(v) / len(v) if v else 0,
                    }
                    for k, v in self._histograms.items()
                }
            }

# Global instance
metrics = SimpleMetrics()
```

**Expose metrics endpoint**:
```python
@router.get("/metrics")
def get_metrics():
    """Get current metrics snapshot."""
    return metrics.get_snapshot()
```

**Key metrics to track**:
- `api_requests_total{endpoint, method, status}` - Request count by endpoint
- `api_request_duration_seconds{endpoint}` - Request latency
- `database_queries_total` - Database query count
- `faiss_searches_total` - FAISS search count
- `faiss_index_size` - Current index size (gauge)
- `errors_total{type}` - Error count by type

**Middleware for automatic tracking**:
```python
from fastapi import Request
import time

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start_time = time.time()

    response = await call_next(request)

    duration = time.time() - start_time
    metrics.increment(
        f"api_requests_total.{request.url.path}.{request.method}.{response.status_code}"
    )
    metrics.observe(
        f"api_request_duration_seconds.{request.url.path}",
        duration
    )

    return response
```

### Exception Handling

**File**: `src/api/exceptions.py` (new)

Define custom exceptions and global exception handlers:

```python
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

class EntityNotFoundError(Exception):
    """Raised when entity is not found."""
    pass

class DuplicateEntityError(Exception):
    """Raised when duplicate entity detected."""
    pass

class ValidationError(Exception):
    """Raised on validation failures."""
    pass

# Register exception handlers in app
@app.exception_handler(EntityNotFoundError)
async def entity_not_found_handler(request: Request, exc: EntityNotFoundError):
    return JSONResponse(
        status_code=404,
        content={"error": "Entity not found", "detail": str(exc)}
    )

@app.exception_handler(DuplicateEntityError)
async def duplicate_entity_handler(request: Request, exc: DuplicateEntityError):
    return JSONResponse(
        status_code=409,
        content={"error": "Duplicate entity", "detail": str(exc)}
    )

@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError):
    return JSONResponse(
        status_code=400,
        content={"error": "Validation failed", "detail": str(exc)}
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    # Log the full exception
    logging.exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": "An unexpected error occurred"}
    )
```

## Implementation Plan

### Step 1: Setup Dependencies
1. Update `pyproject.toml` with new dependencies
2. Run `pip install -e ".[ml]"` to install
3. Verify imports work

### Step 2: Create API Structure
1. Create `src/api/` directory and subdirectories
2. Create all module files with empty/stub implementations
3. Create `src/api_server.py` with minimal FastAPI app

### Step 3: Implement Pydantic Models
1. Define all request/response models in `src/api/models.py`
2. Mirror existing MCP tool schemas
3. Add validation rules

### Step 4: Implement Dependencies
1. Implement `get_db_session()` with request-scoped lifecycle
2. Implement other dependency functions
3. Add FAISS lock mechanism

### Step 5: Implement Categories Router
1. Implement `GET /api/v1/categories`
2. Test manually with curl
3. Verify database session lifecycle

### Step 6: Implement Entries Router
1. Implement read endpoint
2. Implement write endpoint (with inline embedding if enabled)
3. Implement update endpoint
4. Implement delete endpoint
5. Test all CRUD operations
6. Verify FAISS synchronization

### Step 7: Implement Health Check
1. Implement health endpoint with all component checks
2. Test with database down, FAISS unavailable scenarios
3. Verify status codes

### Step 8: Add Observability
1. Implement JSON logging formatter
2. Implement simple metrics collector
3. Add metrics middleware
4. Add metrics endpoint
5. Verify metrics collection

### Step 9: Testing
1. Write unit tests for each router
2. Write integration tests for full request lifecycle
3. Test concurrent requests (use `locust` or `ab`)
4. Verify thread-safety under load
5. Test error handling

### Step 10: Documentation
1. Generate OpenAPI schema (automatic from FastAPI)
2. Add endpoint documentation strings
3. Create deployment guide
4. Document configuration options

## Testing Strategy

### Unit Tests
**Directory**: `tests/api/`

Structure:
```
tests/api/
├── conftest.py              # Pytest fixtures (test client, mock DB)
├── test_categories.py       # Category endpoint tests
├── test_entries.py          # Entry CRUD tests
├── test_health.py           # Health check tests
└── test_dependencies.py     # Dependency injection tests
```

**Example test** (`tests/api/test_categories.py`):
```python
from fastapi.testclient import TestClient
from src.api_server import app

client = TestClient(app)

def test_list_categories():
    response = client.get("/api/v1/categories/")
    assert response.status_code == 200
    data = response.json()
    assert "categories" in data
    assert isinstance(data["categories"], list)
```

### Integration Tests
**File**: `tests/integration/test_api_full_workflow.py`

Test scenarios:
1. Create category → Create experience → Search for it → Update it → Delete it
2. Concurrent writes from multiple threads
3. FAISS index persistence across requests
4. Error handling and recovery

### Load Testing
Use `locust` for basic load testing:

```python
# locustfile.py
from locust import HttpUser, task, between

class APIUser(HttpUser):
    wait_time = between(1, 3)

    @task
    def list_categories(self):
        self.client.get("/api/v1/categories/")

    @task(3)
    def search_entries(self):
        self.client.post("/api/v1/entries/read", json={
            "entity_type": "experience",
            "category_code": "PGS",
            "query": "test query"
        })
```

Run: `locust -f locustfile.py --host=http://localhost:8000`

Target: 50 concurrent users with <500ms p95 latency.

### Manual Verification Steps
1. Start API server: `uvicorn src.api_server:app --reload`
2. Check health: `curl http://localhost:8000/health`
3. List categories: `curl http://localhost:8000/api/v1/categories/`
4. Create entry: `curl -X POST http://localhost:8000/api/v1/entries/write -H "Content-Type: application/json" -d '{"entity_type": "experience", "category_code": "PGS", "data": {...}}'`
5. Verify OpenAPI docs: `http://localhost:8000/docs`

## Acceptance Criteria

- [x] FastAPI server starts successfully and loads all resources
- [x] Health endpoint returns 200 with all components healthy
- [x] All category operations work via HTTP (list)
- [x] All entry operations work via HTTP (read, write, update, delete)
- [x] Search functionality produces same results as MCP implementation
- [x] Concurrent requests (10+ simultaneous) complete without errors
- [x] Database sessions are properly scoped (no leaks)
- [x] FAISS operations are thread-safe (verified under load)
- [x] Metrics endpoint returns request counts and latencies
- [x] JSON logs are written for all requests
- [x] OpenAPI documentation is auto-generated and accessible
- [x] Unit tests achieve >80% code coverage
- [x] Integration tests cover all happy paths and major error cases
- [x] Load test shows stable performance under 50 concurrent users

## Operational Considerations

### Configuration
Add environment variables:
```bash
# API Configuration
CHL_API_HOST=0.0.0.0
CHL_API_PORT=8000
CHL_API_WORKERS=1          # Number of Uvicorn workers (keep at 1 for now)
CHL_API_LOG_LEVEL=info

# Existing variables
CHL_EXPERIENCE_ROOT=/path/to/data
CHL_DATABASE_PATH=chl.db
# (Legacy) CHL_EMBED_ON_WRITE has been removed; vector refresh now runs via explicit FAISS snapshots.
# ... other existing vars
```

### Deployment Steps
1. Install dependencies: `pip install -e ".[ml]"`
2. Set environment variables in `.env` file
3. Initialize database if needed: `python scripts/setup.py`
4. Start API server: `uvicorn src.api_server:app --host 0.0.0.0 --port 8000`
5. Verify health: `curl http://localhost:8000/health`

**For production** (Phase 1 scope limited to single worker):
```bash
uvicorn src.api_server:app \
  --host 0.0.0.0 \
  --port 8000 \
  --log-config logging.yaml \
  --workers 1
```

**Why single worker?**
- SQLite single-writer limitation
- FAISS index is not shared across processes
- Phase 4 will address this with proper multiprocessing support

### Rollback Procedure
If API has issues:
1. Stop API server
2. Users can continue using stdio MCP with direct database access (existing `src/server.py`)
3. No data migration needed (database schema unchanged)
4. Fix issues and restart API

### Monitoring
1. Check health endpoint every 30 seconds
2. Alert if health status is "unhealthy" for >2 minutes
3. Monitor metrics for:
   - Request rate spikes
   - Latency increases (p95 > 1 second)
   - Error rate >5%
4. Monitor log volume for exceptions

## Open Questions

- [ ] Should we add rate limiting in Phase 1? (Recommendation: No, add in Phase 2)
- [ ] Should we support API authentication? (Recommendation: No, local network only for Phase 1)
- [ ] Should we version the API (`/api/v1/` vs `/api/`)? (Recommendation: Yes, already included)
- [ ] Should we add request ID tracking? (Recommendation: Yes, add to logging middleware)
- [ ] Should we expose FAISS index rebuild endpoint? (Recommendation: No, keep as script for Phase 1)

## Dependencies from Other Phases

**Consumed by Phase 2**:
- HTTP endpoints must be stable and well-tested
- Error responses must be predictable for MCP translation
- Health endpoint must be reliable for MCP startup checks

**Consumed by Phase 3**:
- FAISS locking mechanism must be extensible for more complex scenarios
- Search endpoint must support all current search parameters

**Consumed by Phase 4**:
- Endpoint structure must accommodate async embedding status
- Metrics infrastructure must support queue metrics
- Resource initialization must allow adding background workers

## Notes
- Keep this phase focused on establishing the HTTP foundation
- Resist temptation to add queue logic or MCP shim (save for later phases)
- Prioritize correctness and stability over performance optimization
- Document all design decisions for future reference
