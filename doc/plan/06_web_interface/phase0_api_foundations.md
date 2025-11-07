# Phase 0: API Foundations for the Web Interface

## Goals
- Provide HTTP endpoints for configuration, operations (import/export/index rebuild), and worker orchestration that both MCP and the future UI can consume.
- Add safety primitives (advisory locks, validation) that enforce correct sequencing even when requests originate outside the browser.
- Capture telemetry (queue depth, worker heartbeats, operation progress) in a canonical channel that SSE can stream later.

## Success Criteria
- `/api/settings` CRUD endpoints persist configuration metadata (not secrets) into SQLite, validate inputs, and return structured error messages.
- `/api/operations/{import|export|index}` endpoints trigger background jobs, acquire locks, and expose job status so callers can poll.
- `/api/workers` endpoints can pause, resume, drain, and report health for each worker process.
- Telemetry table or in-memory publisher records queue depth, worker state, and job progress at least every 5 seconds.
- FastAPI startup initializes advisory-lock helpers, telemetry publisher, and background cleanup tasks without race conditions.

## Prerequisites
- Existing FastAPI app skeleton from web API project (routers, dependency injection, structured logging).
- Clear understanding of current scripts (`scripts/import.py`, `scripts/export.py`, worker launcher) to extract shared logic.
- Database migration tooling ready (Alembic or handcrafted SQL) to add settings + telemetry tables.

## Implementation Guide

### 1. Schema & Config Metadata
- Add `settings` table with columns: `key`, `value_json`, `checksum`, `validated_at`, `notes`. Store only metadata (e.g., credential path) and retain secrets on disk.
- Add `worker_metrics` (worker_id, heartbeat_at, status, queue_depth, processed, failed, payload JSON for custom counters).
- Provide migration script that populates existing YAML/env config into this table on first run.

### 2. Settings Service & Validation
- Create `src/services/settings_service.py` with methods to read/update typed configs and perform validation hooks (e.g., ping Google Sheets, verify credential path exists, ensure model choice supported).
- Integrate with FastAPI router `src/api/routers/settings.py` for CRUD endpoints:
  - `GET /api/settings` → returns consolidated config.
  - `PUT /api/settings/credentials` → accepts metadata, verifies file path is within managed directory, updates row once validation succeeds.
  - `PUT /api/settings/sheets` → validates sheet IDs by calling Sheets API with stored credentials.

### 3. Operations Orchestrator
- Add `src/services/operations.py` responsible for starting long-running jobs via background tasks or Celery-like worker (initially `asyncio.create_task`).
- Implement advisory lock helper using SQLite `BEGIN IMMEDIATE` or a dedicated `locks` table. Provide `with operation_lock("import")` context manager.
- `/api/operations/import` endpoint checks lock, schedules async job that reuses existing import logic, and writes progress events (percent, current row) to telemetry.
- Similar endpoints for export and index rebuild. Include `status` endpoint returning latest job metadata so UI can poll if SSE unavailable.

### 4. Worker Control Endpoints
- Wrap current worker manager in `WorkerController` class capable of pause/resume/drain.
- `/api/workers` endpoints:
  - `GET /api/workers` → list workers + status.
  - `POST /api/workers/{id}/pause` and `/resume`.
  - `POST /api/workers/drain` → prevents new queue items until queue empty.
- Each action updates telemetry (status change timestamps) for UI visibility.

### 5. Telemetry Publisher
- Run background task that every few seconds gathers queue length (pending embeddings), worker heartbeats, and operation progress.
- Store metrics rows and push to an in-process pub/sub (e.g., `asyncio.Queue`) consumed by SSE router in Phase 3.
- Provide `GET /api/telemetry/snapshot` for polling clients.

### 6. Startup & Lifecycle
- Ensure FastAPI `lifespan` initializes: config loader, settings service, worker controller, telemetry scheduler, operations service.
- Add graceful shutdown hooks to flush telemetry and release locks.

### 7. Documentation & Examples
- Update `README` or dedicated docs to describe new endpoints, required env vars (managed credential dir), and how to migrate existing configs (script or command).

## Testing & Validation
- Unit tests for settings validation (credential path guard, sheet ID verification, model selection rules).
- Integration tests hitting `/api/operations/import` to ensure a second request is rejected while first lock held.
- Simulate worker heartbeats and assert telemetry snapshots aggregate correctly.
- Manual smoke test: run API server, perform sequence (configure sheets → trigger import → view telemetry snapshot).

## Risks & Mitigations
- **Lock starvation**: long-lived locks could block urgent operations → include timeout + cancellation endpoint.
- **Telemetry bloat**: storing every heartbeat could grow DB → implement retention window (e.g., keep 24h, prune older entries).
- **Credential misuse**: ensure settings endpoint only accepts paths within managed directory and enforces file permissions before accepting.
