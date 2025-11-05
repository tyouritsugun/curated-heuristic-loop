# Phase 2: MCP Integration

## Goals
- Convert stdio MCP server into a thin HTTP client shim
- Maintain backward compatibility with existing MCP client integrations
- Implement health gating and circuit breaker patterns for reliability
- Ensure transparent migration (MCP clients see no behavioral changes)
- Provide fallback mechanism if API is unavailable

## Success Criteria
- Existing MCP clients work without configuration changes
- All tool operations are transparently forwarded to the HTTP API
- Health checks prevent MCP server startup if API is unavailable
- Error messages are properly translated from HTTP responses to MCP errors
- Performance overhead of HTTP layer is negligible (<50ms p95)
- Fallback to direct database mode is available for emergency rollback

## Prerequisites
- Phase 1 completed: HTTP API is stable and tested
- All Phase 1 endpoints are validated to produce identical results to current MCP behavior
- API server is deployable and monitored

## Detailed Design

### Components to Modify

#### 1. Rename Current MCP Server
**Action**: Preserve existing direct-database implementation as fallback

```bash
# Rename files
mv src/server.py src/mcp_server_direct.py
```

**Purpose**: Keep current implementation for:
- Emergency rollback if API has issues
- Local development/testing without API
- Reference implementation for behavior validation

#### 2. New MCP Server Shim
**File**: `src/server.py` (rewrite)

The new server becomes a lightweight HTTP client that:
- Initializes HTTP client to API server
- Performs health check on startup
- Translates MCP tool calls to HTTP requests
- Translates HTTP responses back to MCP responses
- Implements retry logic and circuit breaker
- Falls back to error messages if API is down

Structure:
```python
#!/usr/bin/env python3
"""CHL MCP Server - HTTP API Client Shim"""
import httpx
import logging
from fastmcp import FastMCP
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Initialize MCP server
mcp = FastMCP("CHL MCP Server")

# Global HTTP client (initialized on startup)
api_client: Optional[httpx.Client] = None
api_base_url: str = "http://localhost:8000"

# Circuit breaker state
circuit_breaker_open = False
circuit_breaker_failures = 0
CIRCUIT_BREAKER_THRESHOLD = 5

@mcp.tool()
def list_categories() -> Dict[str, Any]:
    """List all available category shelves."""
    return api_request("GET", "/api/v1/categories/")

@mcp.tool()
def read_entries(
    entity_type: str,
    category_code: str,
    query: str = None,
    ids: list = None,
    limit: int = None
) -> Dict[str, Any]:
    """Fetch experiences or manuals by ids or semantic query."""
    payload = {
        "entity_type": entity_type,
        "category_code": category_code,
        "query": query,
        "ids": ids,
        "limit": limit
    }
    return api_request("POST", "/api/v1/entries/read", json=payload)

# ... other tool definitions

def api_request(method: str, path: str, **kwargs) -> Dict[str, Any]:
    """
    Make HTTP request to API server with error handling.

    Implements:
    - Circuit breaker pattern
    - Retry logic with exponential backoff
    - HTTP to MCP error translation
    """
    global circuit_breaker_open, circuit_breaker_failures

    if circuit_breaker_open:
        raise MCPError(
            "API server is currently unavailable (circuit breaker open). "
            "Please try again later or contact support."
        )

    try:
        response = api_client.request(method, f"{api_base_url}{path}", **kwargs)
        response.raise_for_status()

        # Reset circuit breaker on success
        circuit_breaker_failures = 0

        return response.json()

    except httpx.HTTPStatusError as e:
        # Translate HTTP errors to MCP errors
        circuit_breaker_failures += 1
        if circuit_breaker_failures >= CIRCUIT_BREAKER_THRESHOLD:
            circuit_breaker_open = True
            logger.error("Circuit breaker opened after %d failures", circuit_breaker_failures)

        raise translate_http_error(e)

    except httpx.RequestError as e:
        circuit_breaker_failures += 1
        if circuit_breaker_failures >= CIRCUIT_BREAKER_THRESHOLD:
            circuit_breaker_open = True

        raise MCPError(f"Failed to connect to API server: {e}")
```

#### 3. HTTP Client Module
**File**: `src/mcp/api_client.py` (new)

Encapsulate HTTP communication logic:

```python
"""HTTP client for API server communication."""
import httpx
import logging
from typing import Dict, Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

class APIClient:
    """HTTP client for CHL API server with retry and error handling."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip('/')
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "CHL-MCP-Client/1.0"}
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True
    )
    def request(
        self,
        method: str,
        path: str,
        **kwargs
    ) -> httpx.Response:
        """
        Make HTTP request with automatic retry.

        Retries on:
        - Network errors (connection refused, timeout)
        - 503 Service Unavailable
        - 429 Too Many Requests

        Does NOT retry on:
        - 4xx client errors (except 429)
        - 500 Internal Server Error (should be investigated)
        """
        url = f"{self.base_url}{path}"
        logger.debug(f"{method} {url}")

        try:
            response = self.client.request(method, url, **kwargs)

            # Retry on specific status codes
            if response.status_code in (503, 429):
                logger.warning(
                    f"Retryable error {response.status_code} from {url}"
                )
                response.raise_for_status()

            return response

        except httpx.TimeoutException as e:
            logger.error(f"Request timeout for {url}: {e}")
            raise
        except httpx.RequestError as e:
            logger.error(f"Request error for {url}: {e}")
            raise

    def check_health(self) -> Dict[str, Any]:
        """Check API server health."""
        response = self.request("GET", "/health")
        return response.json()

    def close(self):
        """Close HTTP client."""
        self.client.close()
```

#### 3a. Shared API Client for Scripts

**File**: `src/api_client.py` (new)

Lightweight HTTP client wrapper for operational scripts (import, export, rebuild, etc.) to call the API without duplicating request code:

```python
"""Shared API client for operational scripts.

Usage:
    from src.api_client import get_api_client

    client = get_api_client()  # Uses CHL_API_BASE_URL env var

    # Pause queue before bulk import
    client.pause_queue()

    # Wait for queue to drain
    client.drain_queue(timeout=300)

    # Resume queue
    client.resume_queue()
"""
import os
import httpx
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class ScriptAPIClient:
    """Simple API client for operational scripts."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = 30.0):
        self.base_url = (base_url or os.getenv("CHL_API_BASE_URL", "http://localhost:8000")).rstrip('/')
        self.timeout = timeout

    def is_available(self) -> bool:
        """Check if API server is available."""
        try:
            response = httpx.get(f"{self.base_url}/health", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    def pause_queue(self) -> Dict[str, Any]:
        """Pause background workers."""
        response = httpx.post(f"{self.base_url}/admin/queue/pause", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def resume_queue(self) -> Dict[str, Any]:
        """Resume background workers."""
        response = httpx.post(f"{self.base_url}/admin/queue/resume", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def drain_queue(self, timeout: int = 300) -> Dict[str, Any]:
        """Wait for queue to empty."""
        response = httpx.post(
            f"{self.base_url}/admin/queue/drain",
            params={"timeout": timeout},
            timeout=timeout + 10  # Add buffer for HTTP timeout
        )
        response.raise_for_status()
        return response.json()

    def get_queue_status(self) -> Dict[str, Any]:
        """Get queue and worker status."""
        response = httpx.get(f"{self.base_url}/admin/queue/status", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

def get_api_client(base_url: Optional[str] = None) -> ScriptAPIClient:
    """Factory function to get API client with default settings."""
    return ScriptAPIClient(base_url=base_url)
```

**Usage in scripts/import.py**:
```python
from src.api_client import get_api_client

client = get_api_client()

if client.is_available():
    logger.info("API server detected, coordinating import")
    client.pause_queue()
    result = client.drain_queue(timeout=600)

    if result["status"] != "drained":
        logger.warning("Queue did not fully drain: %s remaining", result.get("remaining"))

    # ... perform import

    client.resume_queue()
else:
    logger.warning("API server not available, proceeding with direct database import")
    # ... perform import
```

#### 4. Error Translation Layer
**File**: `src/mcp/errors.py` (new)

Map HTTP status codes to MCP error messages:

```python
"""Error translation between HTTP and MCP."""
from typing import Dict, Any
import httpx

class MCPError(Exception):
    """Base exception for MCP errors."""
    pass

class MCPValidationError(MCPError):
    """Validation error (maps to 400)."""
    pass

class MCPNotFoundError(MCPError):
    """Entity not found (maps to 404)."""
    pass

class MCPConflictError(MCPError):
    """Conflict/duplicate error (maps to 409)."""
    pass

class MCPServerError(MCPError):
    """Internal server error (maps to 500)."""
    pass

def translate_http_error(http_error: httpx.HTTPStatusError) -> MCPError:
    """
    Translate HTTP error to MCP error.

    Error mapping:
    - 400 Bad Request → MCPValidationError
    - 404 Not Found → MCPNotFoundError
    - 409 Conflict → MCPConflictError
    - 500 Internal Server Error → MCPServerError
    - 503 Service Unavailable → MCPServerError (with retry message)
    - Other → MCPError
    """
    status_code = http_error.response.status_code
    response_body = http_error.response.json() if http_error.response.text else {}

    error_detail = response_body.get("detail", str(http_error))

    if status_code == 400:
        return MCPValidationError(f"Validation failed: {error_detail}")
    elif status_code == 404:
        return MCPNotFoundError(f"Not found: {error_detail}")
    elif status_code == 409:
        return MCPConflictError(f"Conflict: {error_detail}")
    elif status_code == 503:
        return MCPServerError(
            f"API server is temporarily unavailable: {error_detail}. "
            "Please try again in a few moments."
        )
    elif status_code >= 500:
        return MCPServerError(f"Server error: {error_detail}")
    else:
        return MCPError(f"API request failed ({status_code}): {error_detail}")
```

### Startup Health Gating

**Purpose**: Prevent MCP server from starting if API is unhealthy.

**Implementation**:
```python
def startup_health_check(api_client: APIClient, max_wait: int = 30) -> bool:
    """
    Check API health on startup.

    Behavior:
    - Wait up to max_wait seconds for API to become healthy
    - Poll health endpoint every 2 seconds
    - Return True if healthy, False otherwise
    """
    import time

    start_time = time.time()
    while time.time() - start_time < max_wait:
        try:
            health = api_client.check_health()
            status = health.get("status")

            if status == "healthy":
                logger.info("API server is healthy")
                return True
            elif status == "degraded":
                logger.warning(
                    "API server is degraded but functional: %s",
                    health.get("components")
                )
                return True  # Allow startup with degraded components
            else:
                logger.warning("API server is unhealthy, retrying...")

        except Exception as e:
            logger.warning(f"Health check failed: {e}, retrying...")

        time.sleep(2)

    logger.error(
        "API server did not become healthy within %d seconds",
        max_wait
    )
    return False

# In startup logic
api_client = APIClient(base_url=config.api_base_url)

if not startup_health_check(api_client):
    logger.error("Cannot start MCP server: API is unavailable")
    sys.exit(1)
```

### Circuit Breaker Pattern

**Purpose**: Prevent cascading failures when API is down.

**Implementation**:
```python
class CircuitBreaker:
    """
    Circuit breaker to prevent cascading failures.

    States:
    - CLOSED: Normal operation
    - OPEN: Too many failures, reject requests immediately
    - HALF_OPEN: Allow one test request after timeout

    Behavior:
    - Opens after `failure_threshold` consecutive failures
    - Stays open for `timeout` seconds
    - Transitions to HALF_OPEN to test recovery
    - Closes if test request succeeds
    """

    def __init__(self, failure_threshold: int = 5, timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failures = 0
        self.state = "CLOSED"
        self.opened_at = None

    def call(self, func, *args, **kwargs):
        if self.state == "OPEN":
            # Check if timeout elapsed
            if time.time() - self.opened_at >= self.timeout:
                logger.info("Circuit breaker transitioning to HALF_OPEN")
                self.state = "HALF_OPEN"
            else:
                raise MCPServerError(
                    "API server is currently unavailable. "
                    f"Please try again in {int(self.timeout - (time.time() - self.opened_at))} seconds."
                )

        try:
            result = func(*args, **kwargs)

            # Success: reset or close circuit
            if self.state == "HALF_OPEN":
                logger.info("Circuit breaker closing after successful test")
                self.state = "CLOSED"
            self.failures = 0

            return result

        except Exception as e:
            self.failures += 1

            if self.failures >= self.failure_threshold:
                logger.error(
                    "Circuit breaker opening after %d failures",
                    self.failures
                )
                self.state = "OPEN"
                self.opened_at = time.time()

            raise
```

### Configuration

Add to `src/config.py`:

```python
class Config:
    # ... existing fields

    # API client configuration
    api_base_url: str = "http://localhost:8000"
    api_timeout: float = 30.0
    api_health_check_max_wait: int = 30
    api_circuit_breaker_threshold: int = 5
    api_circuit_breaker_timeout: int = 60

    # Fallback mode
    use_api: bool = True  # If False, use direct database (emergency fallback)
```

Environment variables:
```bash
CHL_API_BASE_URL=http://localhost:8000
CHL_API_TIMEOUT=30.0
CHL_API_HEALTH_CHECK_MAX_WAIT=30
CHL_USE_API=1  # Set to 0 for direct database fallback
```

### Backward Compatibility Strategy

**Feature Flag Approach**:
```python
# In src/server.py
if config.use_api:
    # Use HTTP API shim (Phase 2)
    from src.mcp.api_client import APIClient
    api_client = APIClient(config.api_base_url)
    # ... implement tools with api_client
else:
    # Use direct database (fallback to Phase 1 behavior)
    logger.warning("Using direct database access (API disabled)")
    from src.mcp_server_direct import mcp as direct_mcp
    # Delegate to direct implementation
```

**Rollback Procedure**:
1. Set `CHL_USE_API=0` in environment
2. Restart MCP server
3. MCP clients now use direct database access (old behavior)
4. No data migration needed

## Implementation Plan

### Step 1: Create HTTP Client Infrastructure
1. Create `src/mcp/api_client.py` with APIClient class (for MCP server)
2. Create `src/api_client.py` with ScriptAPIClient class (for operational scripts)
3. Create `src/mcp/errors.py` with error translation
4. Add retry logic with tenacity
5. Add unit tests

### Step 2: Implement Circuit Breaker
1. Create circuit breaker class
2. Integrate with APIClient
3. Add tests for state transitions

### Step 3: Refactor MCP Server
1. Rename `src/server.py` → `src/mcp_server_direct.py`
2. Update references to `src/server.py` in:
   - `src/config.py:8` (MCP command args)
   - `README.md:92` and `README.md:115` (Claude Desktop config examples)
   - `doc/chl_manual.md:123` (usage instructions)
   - `scripts/setup.py:721` (post-setup instructions)
   - `doc/architecture.md:15` (architecture diagram)
3. Create new `src/server.py` with HTTP shim
4. Implement all tool handlers with API calls
5. Add startup health check
6. Add feature flag for fallback mode

### Step 4: Error Handling
1. Implement error translation for all HTTP status codes
2. Add user-friendly error messages
3. Test error scenarios (API down, timeout, etc.)

### Step 5: Integration Testing
1. Test with API server running (happy path)
2. Test with API server stopped (circuit breaker)
3. Test with API server degraded (fallback behavior)
4. Test error propagation
5. Measure performance overhead
6. Test with real MCP clients (Claude Code, etc.)

### Step 6: Documentation
1. Update deployment guide
2. Document configuration options
3. Document rollback procedure
4. Add troubleshooting guide

## Testing Strategy

### Unit Tests
**File**: `tests/mcp/test_api_client.py`

Test scenarios:
- Successful requests return expected data
- Retry logic triggers on 503/429
- Timeout handling
- Connection errors
- Error translation correctness

**File**: `tests/mcp/test_circuit_breaker.py`

Test scenarios:
- Circuit opens after threshold failures
- Circuit stays open for timeout period
- Circuit transitions to half-open
- Circuit closes after successful test

### Integration Tests
**File**: `tests/integration/test_mcp_api_shim.py`

Test scenarios:
1. Start API server, start MCP, verify all tools work
2. Stop API mid-operation, verify circuit breaker opens
3. Restart API, verify circuit breaker closes
4. Compare results between HTTP shim and direct database
5. Measure latency overhead (should be <50ms p95)

### Manual Verification
1. Start API server: `uvicorn src.api_server:app`
2. Start MCP server: `python src/server.py`
3. Use MCP client to call all tools
4. Stop API server, verify error messages
5. Restart API, verify recovery

## Acceptance Criteria

- [ ] MCP server successfully connects to API on startup
- [ ] Health check prevents startup if API is unavailable
- [ ] All MCP tools work identically to Phase 1 direct-database implementation
- [ ] Error messages are user-friendly and actionable
- [ ] Circuit breaker opens after 5 consecutive failures
- [ ] Circuit breaker closes after API recovers
- [ ] Retry logic handles transient failures (503, network errors)
- [ ] Performance overhead is <50ms p95 for typical operations
- [ ] Fallback mode (`CHL_USE_API=0`) works without code changes
- [ ] No configuration changes required for MCP clients
- [ ] All tests pass with >80% coverage

## Operational Considerations

### Deployment Sequence
1. Deploy API server first (Phase 1)
2. Verify API health endpoint returns 200
3. Update MCP server code to Phase 2 shim
4. Restart MCP server (will connect to API)
5. Verify MCP tools work via API

### Rollback Procedure
If issues arise:
1. Set `CHL_USE_API=0` in environment
2. Restart MCP server
3. MCP now uses direct database (original behavior)
4. Investigate and fix API issues
5. Re-enable API mode when ready

### Monitoring
1. Monitor circuit breaker state (add to health endpoint)
2. Alert if circuit breaker is open for >5 minutes
3. Monitor API client error rate
4. Monitor latency increase (compare to Phase 1 baseline)

### Troubleshooting Guide

**Problem**: MCP server fails to start with "API is unavailable"
- **Solution**: Check API server is running (`curl http://localhost:8000/health`)
- **Workaround**: Set `CHL_USE_API=0` to use direct database

**Problem**: Circuit breaker opens frequently
- **Solution**: Check API server logs for errors, increase `CHL_API_CIRCUIT_BREAKER_THRESHOLD`
- **Workaround**: Restart API server to fix underlying issue

**Problem**: Slow response times
- **Solution**: Check API server performance, increase `CHL_API_TIMEOUT`
- **Investigation**: Compare latency with and without API layer

## Open Questions

- [ ] Should we add authentication tokens for API requests? (Recommendation: Not for Phase 2, API is local network)
- [ ] Should circuit breaker state persist across MCP restarts? (Recommendation: No, fresh start is fine)
- [ ] Should we add request ID propagation for tracing? (Recommendation: Yes, helpful for debugging)
- [ ] Should we cache API responses? (Recommendation: No, adds complexity)

## Dependencies from Other Phases

**Depends on Phase 1**:
- API endpoints must be stable and tested
- Health endpoint must reliably report status
- Error responses must be consistent

**Consumed by Phase 3**:
- HTTP client can forward search parameters
- Error handling covers FAISS-specific errors

**Consumed by Phase 4**:
- Client can handle async embedding status (pending/embedded/failed)
- Retry logic accounts for temporary embedding unavailability

## Notes
- Keep fallback mode (`use_api=False`) for emergency rollback
- Circuit breaker prevents cascading failures but adds complexity
- Performance overhead should be negligible for typical workloads
- Consider adding request ID tracking for debugging (add in implementation)
