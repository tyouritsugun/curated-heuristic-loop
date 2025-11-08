# Web API + stdio MCP Architecture

## Purpose
- Give every MCP client access to the same data, search, and embedding capabilities without breaking existing stdio integrations.
- Lower memory and maintenance costs by running heavy services once and sharing them through an HTTP interface.
- Unlock async processing so MCP responses stay fast even when embeddings or indexing take longer.

## Current Pain Points
- Each client launches its own process, reloading models and indexes and producing diverging state.
- Concurrency limits in SQLite and FAISS make write conflicts and stale search results common.
- Embedding work blocks stdio responses, so large updates or re-indexes feel slow and unreliable.

## Target Architecture
- Retain a thin stdio MCP process per client that translates tool calls into HTTP requests.
- Add a single API process that owns SQLite, FAISS, the embedding model, and a lightweight job queue.
- Communicate over REST (or similar) so tooling, tests, and future services can call the same boundary.

```
MCP Client → stdio MCP shim → HTTP API → shared storage + compute
```

## Guiding Principles
- Keep MCP contracts stable; clients should not need new configuration.
- Prefer stateless HTTP handlers backed by shared infrastructure over process-local logic.
- Design for observability and recoverability first; performance tuning comes later.
- Make future horizontal scaling possible but optimise for single-machine reliability today.

## Scope
**In scope**
- New HTTP service exposing CRUD, search, embedding, and health capabilities already present in the codebase.
- Refactoring the MCP server to consume the HTTP API instead of touching storage directly.
- Introducing an async queue so embedding work happens off the stdio request path.

**Out of scope**
- Changing the wire protocol away from stdio.
- Migrating storage engines (SQLite + FAISS stay in place for now).
- Multi-machine deployment or cloud-specific concerns.

## Component Overview
- **MCP layer**: Handles stdio lifecycle, validates tool requests, and forwards them to the API. Performs health checks on startup and on failure recovery.
- **API layer**: Serves HTTP endpoints, centralises database/index access, and publishes health metrics. (Async embedding workers were archived in Nov 2025; vector refresh now runs via explicit FAISS snapshots.)
- **Shared services**: SQLite connection pool, FAISS index manager, embedding model instance, and a configurable async queue.
- **Observability**: Basic health endpoint plus counters/timers for queue depth, embedding latency, and index freshness.

## Source Layout (Current + Planned)
- `src/server.py` (to be renamed `src/mcp_server.py`) - stdio MCP entrypoint that becomes a thin shim around the HTTP client.
- `src/api_server.py` (new) - FastAPI entrypoint that serves the shared HTTP surface.
- `src/mcp/` - protocol handlers, request/response models, and the API client adapter used by the stdio process.
- `src/api/` (new) - FastAPI app, routers, and dependency wiring that expose CRUD/search/queue endpoints.
- `src/api_client.py` (new) - Shared HTTP client module for operational scripts to call the API without duplicating request code.
- `src/storage/` - SQLite schema, repositories, and import/export helpers consumed by both layers.
- `src/search/` - FAISS index management and vector search providers moved behind API-friendly interfaces.
- `src/embedding/` - Embedding and reranker clients for query-time vectorisation (the legacy `EmbeddingService` was removed alongside the worker pool).
- `src/background/` - *Archived.* The queue primitives described in Phase 4 were removed when the project switched to manual FAISS snapshot management.
- `scripts/` - Operational utilities (import/export, index rebuild, embedding sync). Until the API is authoritative, they may talk directly to SQLite/FAISS; once the HTTP layer is ready, migrate them onto the API or require downtime while they run.
- `scripts/tweak/` - Lightweight CLIs for debugging read/write paths; add an API-backed mode while temporarily preserving direct-database access for local debugging until the HTTP paths are stable.
- `tests/` - Split suites (`tests/mcp/`, `tests/api/`, `tests/integration/`) covering the stdio shim, HTTP layer, and end-to-end flows.
- We avoid `__init__.py` and `__main__.py` files to keep modules simple and explicit; each entrypoint is a standalone module.

## Operational Considerations
- API start-up should validate database connectivity, load or rebuild the FAISS index, and warm the embedding model.
- Background workers need bounded concurrency and clear failure logging; manual requeue endpoints are sufficient initially.
- File locks or similar coordination keep FAISS updates consistent when multiple jobs write in quick succession.
- Provide simple status commands (CLI or HTTP) so on-call engineers can confirm the system state without attaching a debugger.
- Limit access to the API server to the local network; additional auth is unnecessary for the initial deployment footprint.
- Use explicit `embedding_status` flags (`pending`, `embedded`, `failed`) so writes can commit quickly while vector refresh runs out-of-band. Status flags still matter for telemetry even though the legacy worker pool was removed.
- If we reintroduce workers later, they must be idempotent: dedupe outstanding jobs, allow safe retries, and verify before overwriting vectors so double-processing does not corrupt the index.
- During bulk imports (for example, loading data from Google Sheets), drain the queue and stop (or require the operator to stop) the API server so the database and FAISS index can be rebuilt cleanly before traffic resumes.
- Spreadsheet imports should treat `embedding_status` as write-only metadata: ignore incoming values from Google Sheets and reset all imported rows to `pending` so the post-import job queue can regenerate embeddings deterministically.
- Keep developer tooling ergonomic: the `scripts/tweak/` utilities should gain an API-backed mode while temporarily preserving direct-database access for local debugging until the HTTP paths are stable.

## Delivery Plan
- **Phase 1 - HTTP API foundation**: Stand up the FastAPI service with CRUD, search scaffolding, health checks, and basic tests. Ensure shared resources load once and survive hot reloads.
- **Phase 2 - MCP integration**: Point MCP handlers at the API client, add health gating during startup, and translate HTTP errors back into MCP responses. All existing tools must behave the same from a user perspective.
- **Phase 3 - Shared search & indexing**: Migrate FAISS management into the API, expose a simple search endpoint, and confirm that data written by one client surfaces immediately for all others.
- **Phase 4 - Async embedding queue**: Introduce the in-process queue, enqueue embeddings on write/update, and expose lightweight status/metrics endpoints. Focus on resilience rather than retry automation for v1.

### Detailed Phase Specifications
For implementation details, see the phase-specific specifications:
- [Phase 1: HTTP API Foundation](./05_web_api/phase1_http_api_foundation.md) - FastAPI service, dependencies, routers, health checks, observability
- [Phase 2: MCP Integration](./05_web_api/phase2_mcp_integration.md) - HTTP client shim, error translation, circuit breaker, backward compatibility
- [Phase 3: Shared Search & Indexing](./05_web_api/phase3_shared_search_indexing.md) - FAISS locking, persistence strategy, concurrency control, recovery procedures
- [Phase 4: Async Embedding Queue](./05_web_api/phase4_async_embedding_queue.md) - Background workers, queue management, admin endpoints, bulk import coordination

## Risks & Mitigations
- **API downtime blocks all clients** → Keep MCP-side circuit breakers and clear error messaging; document manual restart steps.
- **Queue overload or stuck jobs** → Cap queue size, surface queue depth in metrics, and allow manual draining.
- **FAISS corruption during writes** → Use lock-based updates and keep a periodic snapshot for quick restore.
- **Slow adoption inside MCP codebase** → Provide a minimal API client abstraction so handler refactors stay incremental.
