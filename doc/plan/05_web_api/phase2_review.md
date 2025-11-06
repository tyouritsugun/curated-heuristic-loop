# Phase 2 MCP Integration – Review

## Findings

1. **Critical – SearchService keeps a long-lived session that is reused across threads** (`src/api_server.py:80-152`, `src/search/service.py:25-86`): the FastAPI lifespan initializes a single `app_session = db.get_session()` and injects it into `SearchService`/`VectorFAISSProvider`. FastAPI executes sync routes inside a threadpool, so every request shares that same SQLAlchemy session instance. SQLAlchemy sessions are not thread-safe and also cache data aggressively, so this leads to race conditions (concurrent requests touching the same session mutate shared state) and stale reads once another request commits new rows. It also means the session can be closed by one thread while another is still using it. Recommendation: stop storing a live Session on `SearchService`; instead inject a sessionmaker / `db.session_factory` and call it per request (or derive the request-scoped session from the FastAPI dependency and pass it into search operations).

2. **Major – Circuit breaker opens on any MCP error, including user mistakes** (`src/mcp/api_client.py:61-72`): `CircuitBreaker.call` increments the failure counter for every exception. When the API returns a 400/404 (translated to `MCPValidationError`/`MCPNotFoundError`), the breaker still counts it toward the threshold and eventually opens, blocking all subsequent traffic for the timeout window. The circuit should only trip on transport issues or server-side failures (e.g., `MCPServerError`, request exceptions). Please filter the exception types before incrementing.

3. **Major – Retry decorator never retries 503/429 responses** (`src/mcp/api_client.py:106-142`): `_make_request` raises `httpx.HTTPStatusError` for 503/429, but the Tenacity `retry_if_exception_type` only lists `TimeoutException` and `ConnectError`. As a result, the retry policy described in the design doc never triggers. Either include `httpx.HTTPStatusError` (and inspect status inside the retry predicate) or move the retry logic out of Tenacity and loop manually.

4. **Major – Script API client calls endpoints that do not exist** (`src/api_client.py:37-70`): the new `ScriptAPIClient` exposes `pause_queue`, `resume_queue`, `drain_queue`, and `get_queue_status`, but there are no matching `/admin/queue/*` routes in the FastAPI app. Any script adopting this helper will get 404s. Please either add the admin router or remove/stub these methods until the endpoints land.

5. **Minor – Logging handler duplication on reloads** (`src/server.py:100-139`): `_setup_logging` collects existing handler targets, but it compares the `Path` object (`log_path`) to a set of strings. That comparison always fails, so each initialization adds another rotating file handler and you eventually emit duplicate log lines. Convert `log_path` to `str` (the direct-mode server already does this) before the membership test.

## Testing & Coverage Gaps

- There are no automated tests for the new MCP HTTP client, circuit breaker, or the API fallback flows outlined in the Phase 2 plan. Please add unit tests for happy-path calls, error translation, retry behaviour, and breaker state transitions, plus at least one integration smoke test that boots the shim against the API.

## Redundant / Dead Code

- `src/api_client.py:1-73` – `ScriptAPIClient` is unused and its `/admin/queue/*` helpers point at non-existent routes, so the entire module is dead weight until the admin API exists.
- `src/mcp/api_client.py:96-104` – `_should_retry` is never referenced; Tenacity handles retry decisions directly, making this helper redundant.
- `src/mcp/errors.py:1-36` – the `Dict`/`Any` imports aren’t used; trimming them keeps the module clean.
