# Phase 1: MCP HTTP Client Migration

> **Note**: This document describes the historical planning for MCP HTTP client migration. The MCP server now operates exclusively in HTTP mode. Direct database mode has been removed.

## Goals
- Replace direct-database MCP server interactions with HTTP calls to the Phase 0 API while keeping feature parity.
- Ensure MCP latencies remain acceptable by batching or caching where needed.

## Success Criteria
- All MCP tools (list categories, read/write entries, run imports, etc.) call HTTP endpoints exclusively.
- Performance baseline: MCP operations over HTTP add <10% latency compared to local DB access for common workflows (list categories, read entries, write entry).
- Error handling translates HTTP errors into MCP-compliant responses with actionable messages.

## Prerequisites
- Phase 0 API endpoints deployed and covered by automated tests.
- Clear mapping from MCP tool names to HTTP URLs + payload schemas.
- Observability hooks (logging, metrics) available on both MCP side and API side.

## Implementation Guide

### 1. HTTP Client Abstraction
- `src/mcp/api_client.py` wraps FastAPI calls with retry/backoff (tenacity) plus a small circuit breaker so repeated failures short‑circuit instead of hammering the API.
- Methods mirror existing repository calls: `list_categories`, `read_entries`, `write_entry`, `run_import`, etc., returning parsed JSON blobs so handlers remain thin.
- Use a single `httpx.Client` instance with keep-alive; log every request via `http_request method=... path=... status=... duration_ms=...`.
- Centralize serialization/deserialization and HTTP→MCP error translation (`MCPTransportError`, `MCPValidationError`, etc.) to keep handler code consistent.

### 2. Feature Flag Wiring
- `CHL_MCP_HTTP_MODE` accepts `http`, `auto`, or `direct` (legacy). Legacy `CHL_USE_API=0` forces `direct` for backward compatibility.
- CLI flag `--chl-http-mode={http|auto|direct}` overrides env vars per invocation so users can flip modes without editing config files.
- `auto` mode wraps `_request_with_fallback`: network errors raise `MCPTransportError`, log a warning, then transparently invoke the legacy handler for that tool. Pure `http` mode propagates the transport error so the user fixes their API server.
- `CHL_API_CIRCUIT_BREAKER_THRESHOLD` / `_TIMEOUT` knobs cap cascading failures, while `CHL_API_HEALTH_CHECK_MAX_WAIT` governs initial readiness.
- Tests can set `CHL_SKIP_MCP_AUTOSTART=1` to import `src.server` without immediately spinning up HTTP mode—fixtures inject fake API clients before calling `init_server()`.

### 3. MCP Tool Adaptation
- Update each MCP handler to call the HTTP client abstraction rather than repositories:
  - `list_categories` → `client.list_categories()`.
  - `read_entries` → passes through query parameters.
  - `write_entry`/`update_entry`/`delete_entry` → call HTTP endpoints, propagate validation errors.
  - Operational tools (import/export) call `/api/operations/...` endpoints and include job status in MCP response.
- Cache read-mostly payloads (e.g., `list_categories`) for ~30 seconds to prevent repeated HTTP chatter during a single IDE session.
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
- Add structured logs on MCP side for each HTTP call (method, path, latency, status) plus explicit warnings when fallback from HTTP→direct triggers.
- Emit metrics (e.g., via StatsD or Prometheus exporter) for success/failure counts while flag is on, aiding rollout monitoring. The circuit breaker should log OPEN/HALF\_OPEN transitions for production debugging.

### 7. Documentation
- Update `doc/plan/06_web_interface.md` (already references Phase 1) with pointers to new env vars and risk mitigations.
- Add section to `README` showing how to switch modes and troubleshoot (e.g., API server not running).

## Testing & Validation
- Unit tests for HTTP client covering retries, error mapping, and serialization.
- Integration tests (`tests/integration/test_mcp_http_mode.py`) run MCP commands against a live FastAPI instance (or a stub) to ensure parity across `http`, `auto`, and `direct`. Each test sets `CHL_SKIP_MCP_AUTOSTART=1` before importing `src.server`.
- Regression suite comparing outputs between legacy and HTTP modes for a fixed dataset.

## Risks & Mitigations
- **API downtime breaks MCP** → ship fallback flag + clear error messages prompting user to start API server.
- **Latency spikes** → add client-side caching, consider local short-circuit for read-only operations if API unreachable.
- **Schema drift** → generate Pydantic models from shared module or publish OpenAPI client to keep MCP and API in sync.
