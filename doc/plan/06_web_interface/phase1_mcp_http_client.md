# Phase 1: MCP HTTP Client Migration

## Goals
- Replace direct-database MCP server interactions with HTTP calls to the Phase 0 API while keeping feature parity.
- Provide a kill switch/feature flag to fall back to the legacy code path if regressions occur.
- Ensure MCP latencies remain acceptable by batching or caching where needed.

## Success Criteria
- All MCP tools (list categories, read/write entries, run imports, etc.) call HTTP endpoints exclusively when `CHL_MCP_HTTP_MODE=1`.
- Feature flag `CHL_MCP_HTTP_MODE` defaults to on for new installs but can be disabled via env var or CLI flag, restoring legacy behavior without redeploy.
- Performance baseline: MCP operations over HTTP add <10% latency compared to local DB access for common workflows (list categories, read entries, write entry).
- Error handling translates HTTP errors into MCP-compliant responses with actionable messages.

## Prerequisites
- Phase 0 API endpoints deployed and covered by automated tests.
- Clear mapping from MCP tool names to HTTP URLs + payload schemas.
- Observability hooks (logging, metrics) available on both MCP side and API side.

## Implementation Guide

### 1. HTTP Client Abstraction
- Create `src/mcp/http_client.py` encapsulating FastAPI calls with retry/backoff and JSON schema conversions.
- Methods mirror existing repository calls: `list_categories`, `read_entries`, `write_entry`, `run_import`, etc.
- Use `httpx.AsyncClient` or `requests` depending on MCP runtime; prefer async if MCP already async-friendly.
- Centralize serialization/deserialization to keep MCP handlers slim.

### 2. Feature Flag Wiring
- Add env var + CLI flag to toggle HTTP mode. Example: `CHL_MCP_HTTP_MODE=0` keeps old behavior.
- At startup, log which mode is active and which base URL the HTTP client targets (default `http://127.0.0.1:8000`).
- Provide fallback path: if HTTP call fails (connection refused) and flag set to `auto`, drop back to direct DB for that request and emit warning.

### 3. MCP Tool Adaptation
- Update each MCP handler to call the HTTP client abstraction rather than repositories:
  - `list_categories` → `client.list_categories()`.
  - `read_entries` → passes through query parameters.
  - `write_entry`/`update_entry`/`delete_entry` → call HTTP endpoints, propagate validation errors.
  - Operational tools (import/export) call `/api/operations/...` endpoints and include job status in MCP response.
- Ensure streaming/large payload scenarios use pagination or chunking to avoid memory spikes.

### 4. Error Translation
- Map HTTP status codes to MCP errors:
  - 400 → `InvalidRequest` with detail from API.
  - 409 → `Conflict` (e.g., concurrent operation lock held).
  - 422 → validation errors enumerated in the response.
  - 500 → `InternalError` prompting user to inspect server logs.
- Include remediation hints so MCP users know to visit the web UI for more context.

### 5. Performance Considerations
- Cache frequently-read data (category list, settings snapshot) inside the MCP process with short TTL (e.g., 30 seconds) to limit HTTP chatter.
- Enable HTTP keep-alive and reuse the client object per MCP process.
- Benchmark common flows locally, compare to baseline, and document results.

### 6. Observability & Logging
- Add structured logs on MCP side for each HTTP call (method, path, latency, status).
- Emit metrics (e.g., via StatsD or Prometheus exporter) for success/failure counts while flag is on, aiding rollout monitoring.

### 7. Documentation
- Update `doc/plan/06_web_interface.md` (already references Phase 1) with pointers to new env vars and risk mitigations.
- Add section to `README` showing how to switch modes and troubleshoot (e.g., API server not running).

## Testing & Validation
- Unit tests for HTTP client covering retries, error mapping, and serialization.
- Integration tests running MCP commands against a live FastAPI instance (can use docker-compose or pytest fixtures) to ensure parity.
- Regression suite comparing outputs between legacy and HTTP modes for a fixed dataset.

## Risks & Mitigations
- **API downtime breaks MCP** → ship fallback flag + clear error messages prompting user to start API server.
- **Latency spikes** → add client-side caching, consider local short-circuit for read-only operations if API unreachable.
- **Schema drift** → generate Pydantic models from shared module or publish OpenAPI client to keep MCP and API in sync.
