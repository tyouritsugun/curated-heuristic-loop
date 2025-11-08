# Phase 3: Core Operations & UX

## Goals
- Build browser pages for import/export control, optional worker management, queue monitoring, and index maintenance using the Phase 0 APIs.
- Surface real-time telemetry via SSE/htmx so operators can see progress without CLI.
- Add UX polish (progress bars, confirmations, inline errors) that makes routine operations safe for non-programmers.

## Success Criteria
- Consolidated operations dashboard (single `/operations` page) exposes import/export/index actions, worker controls, queue health, and job history in one view with advisory locks enforced by the Phase 0 API.
- Worker card shows live status for each worker heartbeat and surfaces pause/resume/drain controls that return inline feedback; draining accepts a timeout parameter and reports remaining items.
- Queue monitor card renders pending/failed counts for experiences/manuals, refreshes automatically via SSE every few seconds, and supports manual refresh by emitting `ops-refresh` events after user actions.
- Job history table lists recent jobs with status colors plus inline cancel buttons for queued/running work; SSE keeps the table in sync without page reloads.
- Settings diagnostics from Phase 2 appear as warning banners (e.g., invalid credentials) to explain why operations might fail, keeping secrets on disk while metadata shows up in SQLite.

## Prerequisites
- Phase 0 telemetry + operations endpoints functioning and emitting data.
- Phase 2 settings UI deployed; credentials and sheet IDs can be managed via browser.
- SSE support available in FastAPI (e.g., `EventSourceResponse`) and htmx configured to consume events.

## Implementation Guide

### 1. Page Layout & Navigation
- Base template now includes a persistent nav bar (CHL brand + Settings/Operations tabs) so operators can jump between configuration and runtime views.
- `/operations` renders a single dashboard with sections for controls, workers, queue metrics, and job history; the heading reiterates localhost-only posture.
- A lightweight onboarding card (“How to use this page”) sits under the flash banner summarizing the recommended flow (run jobs → manage workers → watch telemetry → handle FAISS snapshots) so README instructions are no longer required.
- Context carries `active_page` so nav items highlight automatically.

### 2. Operation Controls
- A single card hosts import/export/index buttons. Each button fires an htmx POST to `/ui/operations/run/{type}` and swaps the card HTML back in.
- Responses emit `HX-Trigger: ops-refresh`, prompting the other cards (and SSE stream) to refetch partials immediately instead of waiting for the next tick.
- The card now shows the latest run per job type (import/export/index) with actor, timestamp, duration, and status badge so operators know what ran last even if they join mid-cycle.
- Under the hood the Phase 0 OperationsService runs the CLI helpers (`scripts/import.py`, `scripts/export.py`, `scripts/rebuild_index.py`) whenever `CHL_OPERATIONS_MODE=scripts` (default); set the env var to `noop` in CI/tests to keep the buttons inert.

### 3. Worker Dashboard
- Workers card lists current workers from telemetry snapshots (heartbeat, processed, failed counts). When no workers are registered, the card explains that the embedding service is offline.
- Pause/resume/drain buttons call new UI wrapper endpoints (`/ui/workers/{action}`) so responses stay HTML-aware and can raise friendly errors if the pool is unavailable.
- Drain form captures a timeout (defaults to 300s) and returns status/remaining counts once the queue clears or the timer expires.

### 4. Queue Monitoring
- The queue card summarizes pending/failed totals (overall + experiences/manuals) and shows the timestamp of the latest telemetry sample.
- Minimal CSS “stat” blocks replace charts for now; future iterations can add spark lines or chart.js if needed.
- Card swaps via SSE (`event: queue`) and can still respond to manual refresh triggers.

### 5. Index Management
- Rebuild index currently shares the generic operations card (button posts to `/ui/operations/run/index`).
- Dedicated snapshot tooling lets operators download the `.index/.meta/.backup` set as a ZIP or upload a ZIP (512 MiB cap) that replaces those files under `CHL_FAISS_INDEX_PATH`, logs an audit entry, and attempts to hot-reload the in-memory FAISS manager. Upload validation mirrors the credential flow (size/type checks, managed directory, SSE refresh).

### 6. Real-time Feedback
- `/ui/stream/telemetry` streams pre-rendered HTML snippets for queue, worker, and job cards using `sse-starlette`. htmx SSE extension swaps the corresponding DOM nodes when `event: queue/workers/jobs` arrive.
- Each loop also sleeps 5s to avoid flooding; tests reuse `?cycles=1` to request a single batch for deterministic assertions.
- When SSE is unavailable the cards still refresh via `hx-trigger="load, ops-refresh"` plus manual refresh events emitted from UI actions.

### 7. UX Polish & Accessibility
- Add confirmation modals for destructive actions using dialog element or small JS helper.
- Provide toast/alert component for success/failure messages with auto-dismiss.
- Keep the onboarding card concise and responsive; collapse into expandable details if future sections crowd above the fold.
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
