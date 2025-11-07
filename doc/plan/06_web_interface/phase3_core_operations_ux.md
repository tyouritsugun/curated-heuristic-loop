# Phase 3: Core Operations & UX

## Goals
- Build browser pages for import/export control, worker management, queue monitoring, and index maintenance using the Phase 0 APIs.
- Surface real-time telemetry via SSE/htmx so operators can see progress without CLI.
- Add UX polish (progress bars, confirmations, inline errors) that makes routine operations safe for non-programmers.

## Success Criteria
- Import, export, and index pages allow triggering operations, show live progress, and enforce locking (buttons disabled when jobs running).
- Worker dashboard lists all workers, indicates status (running/paused/draining), and supports pause/resume/drain actions with confirmation modals.
- Queue monitor displays pending/failed counts, recent errors, and embeds charts/tables that update via SSE every few seconds.
- Index management card shows stats (vector count, last rebuild duration) and supports download/upload of FAISS snapshots with safeguards.
- UI reflects validation state from Phase 2 settings (e.g., warning banners when credentials invalid) so users know why buttons are disabled.

## Prerequisites
- Phase 0 telemetry + operations endpoints functioning and emitting data.
- Phase 2 settings UI deployed; credentials and sheet IDs can be managed via browser.
- SSE support available in FastAPI (e.g., `EventSourceResponse`) and htmx configured to consume events.

## Implementation Guide

### 1. Page Layout & Navigation
- Add top-level navigation (Settings, Import, Export, Workers, Queue, Index) in base template.
- Provide breadcrumb/status bar showing current environment (local, staging) and last sync time.

### 2. Import/Export Pages
- Cards summarizing last run (timestamp, duration, outcome, initiated by) by querying telemetry/job history tables.
- Primary buttons trigger POST to `/api/operations/import|export`; use htmx to swap in progress component that:
  - Shows percent complete based on telemetry events.
  - Offers cancel button if supported, otherwise explains lock will auto-release.
- Display worker auto-pause/resume status retrieved from worker controller.

### 3. Worker Dashboard
- Table of workers with columns: ID, role, status, queue depth processed, last heartbeat, actions.
- Actions call `/api/workers/{id}/pause|resume`; use confirmation dialog for disruptive actions (pause all, drain queue).
- Badge for “Telemetry stale” when heartbeat older than threshold.

### 4. Queue Monitoring
- Visualize pending/failed counts using lightweight charts (could be pure CSS bars or Chart.js if acceptable).
- Surface list of most recent failures with links to logs; data pulled from telemetry table or dedicated failure log endpoint.
- Provide filters (time window) implemented via query params + htmx swaps.

### 5. Index Management
- Show vector count, disk size, last rebuild time, and checksum from Phase 0 metadata.
- Buttons for “Trigger Rebuild” and “Download Snapshot” (forces workers paused).
- Upload form for restoring an index file: enforces extension/size, writes to disk, and registers metadata.

### 6. Real-time Feedback
- Implement `/ui/sse` endpoint streaming JSON events (telemetry, job progress). htmx `hx-ext="sse"` can target DOM fragments.
- Ensure SSE channel batches events per topic to avoid flooding the browser.
- Fall back to periodic polling when SSE unsupported.

### 7. UX Polish & Accessibility
- Add confirmation modals for destructive actions using dialog element or small JS helper.
- Provide toast/alert component for success/failure messages with auto-dismiss.
- Keep forms keyboard-accessible; ensure color choices meet contrast guidelines for status badges.

## Testing & Validation
- Manual walkthrough for each page: trigger operations, pause/resume workers, watch SSE updates.
- Integration tests using Playwright or Selenium (optional) to ensure critical workflows succeed in Chrome/Firefox.
- Backend tests verifying SSE payload structure and rate limiting.
- Load test SSE stream to ensure server can handle multiple browser tabs without starving workers.

## Risks & Mitigations
- **SSE disconnects** → implement auto-reconnect logic on frontend and resume from last event ID.
- **Long-running downloads/uploads** → stream responses and enforce size checks; warn users before pausing workers.
- **Telemetry lag** → cap queue-length queries to avoid heavy scans; consider caching in memory with short TTL.
