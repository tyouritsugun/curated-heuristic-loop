# Web Interface for User Operations

## Purpose
- Remove JSON configuration barriers and make CHL accessible to users with minimal technical background.
- Provide visual control over import/export, worker management, and system settings through a browser interface.
- Enable real-time monitoring of embedding queue, worker health, and index status without CLI expertise.
- Create foundation for future curator tools (entry review, duplicate detection, analytics) in a collaborative web environment.

## Current Pain Points
- New users must manually edit MCP configuration JSON files in unfamiliar locations.
- Environment variables, YAML configs, and CLI flags create multiple sources of truth and confusion.
- Import/export requires running scripts with cryptic flags and understanding worker coordination manually.
- No visibility into system state (queue depth, worker status, index health) without running CLI commands.
- Setup documentation requires understanding of Python environments, MCP protocol, and server architecture.
- Non-programmers cannot use CHL even though the core value (capturing and reusing heuristics) applies to all roles.

## Target Architecture
- Add web UI served by the existing FastAPI server for all operational tasks.
- Keep MCP server as a thin client that forwards requests to the HTTP API.
- Centralize configuration in the web interface (Google credentials, sheet IDs, model selection).
- Provide browser-based controls for import, export, worker pause/resume, and index rebuild.
- Expose real-time status dashboards for queue depth, worker health, and processing metrics.

```
User → Web Browser → FastAPI (Web UI + API endpoints)
                            ↓
                     Shared SQLite + FAISS + Workers

Code Assistant → MCP (thin HTTP client) → FastAPI (same API endpoints)
                                               ↓
                                        Shared SQLite + FAISS + Workers
```

## Guiding Principles
- Prioritize ease of onboarding: users should configure the system entirely through the browser.
- Keep the API server as the single source of authority for configuration, operations, and state.
- Design UI for non-programmers first; advanced users can still use API endpoints or scripts if preferred.
- Make operations safe by default: UI enforces coordination (disable import while workers running, confirm destructive actions).
- Maintain backward compatibility during migration: support both direct-database and HTTP modes temporarily.

## Scope
**In scope**
- Web pages for import/export with one-click execution and progress feedback.
- Worker control dashboard (pause, resume, drain queue, view status).
- Settings page that guides users through credential placement (upload helper or pointing at an existing local file), sheet ID configuration, and embedding model selection.
- Real-time queue monitoring showing pending/failed counts and worker metrics.
- Index management UI (trigger rebuild, view statistics, download/upload index files).

**Out of scope**
- Advanced curator tools (entry review, merging, tagging) - deferred to later phase.
- Multi-user authentication and authorization - assume single-user local deployment initially.
- Mobile-responsive design optimization - desktop browser is primary target.
- Internationalization and accessibility features - focus on core functionality first.
- Distributed or cloud deployment scenarios - single-machine setup only.

**Deployment assumptions**
- FastAPI binds to `127.0.0.1` by default; exposing it beyond the local machine requires the user to place a reverse proxy with their own authentication in front of it to protect credential upload and worker control endpoints.

## Implementation Status (November 2025)
- **Phase 0** – API endpoints for settings, workers, operations, telemetry, audit logging, and advisory locks are live; SQLite stores only metadata while credential/index bytes stay on disk.
- **Phase 1** – MCP server speaks HTTP (`CHL_MCP_HTTP_MODE=http|auto|direct`), caches hot data, and falls back to direct handlers when the API is unreachable.
- **Phase 2** – `/settings` shipped with credential upload/path helpers, sheet/model forms, diagnostics, audit feed, and metadata backup/restore; secrets remain filesystem-only.
- **Phase 3 (current)** – `/operations` dashboard streams queue/worker/job cards via SSE, shows per-job-type summaries (actor/timestamp/duration), exposes worker pause/resume/drain, and adds an index snapshot card that downloads or uploads FAISS artifacts (ZIP, 512 MiB cap) with audit logging and hot-reload attempts. Triggered actions emit `ops-refresh` so htmx/SSE keep the UI in sync without reloads.
- `/operations` buttons call the same CLI helpers (`scripts/import.py`, `scripts/export.py`, `scripts/rebuild_index.py`) whenever `CHL_OPERATIONS_MODE=scripts` (default). Set the env var to `noop` for CI/tests if you want the buttons to stay inert.
- Worker controls remain optional: the card automatically hides its buttons and displays guidance when no external embedding pool is registered, steering operators toward the FAISS snapshot workflow.

## Component Overview
- **Web UI layer**: HTML templates or static assets served by FastAPI, communicating with API endpoints via fetch/AJAX.
- **API endpoints**: Extend existing FastAPI routers to support UI operations (upload credentials, register existing credential paths, trigger import, configure settings).
- **Configuration storage**: Move configuration metadata from YAML/env vars into SQLite (new settings table) while keeping sensitive blobs (Google credentials, FAISS indexes) as files on disk. The DB only stores canonical paths, checksums, and validation timestamps so the runtime keeps reading secrets from the filesystem without duplicating them.
- **Real-time updates**: Server-sent events or WebSocket for live queue status and worker metrics.
- **MCP migration**: Refactor MCP server from direct database access to HTTP client forwarding requests to local API.
- **Telemetry pipeline**: Workers emit periodic heartbeats and queue counters into a lightweight metrics table or in-process broadcast channel; FastAPI consumes that data when streaming live dashboards.

## Source Layout
- `src/api_server.py` - Add static file serving and template rendering alongside existing API routes.
- `src/api/routers/ui.py` (new) - UI-specific endpoints for file uploads, configuration changes, and operation triggers.
- `src/api/routers/settings.py` (new) - CRUD endpoints for system configuration stored in database.
- `src/storage/schema.py` - Add settings table for persistent configuration (credentials path, sheet IDs, model choices).
- `src/web/` (new) - Static assets (HTML, CSS, JavaScript) for the web interface.
- `src/mcp/api_client.py` - HTTP client abstraction with retry/circuit-breaker logic so MCP calls the API instead of SQLite.
- `src/server.py` - Refactor to use HTTP client for all operations, keeping only MCP protocol translation logic (plus direct fallback handlers).
- `tests/integration/test_mcp_http_mode.py` - Coverage for HTTP/auto/direct toggles, caching, and transport failures.
- `scripts/` - Gradually migrate operational scripts to call API endpoints instead of direct database access.

## User Experience Flow
**Initial Setup**
1. Clone repository and run single command to start API server.
2. Open browser to localhost:8000, see welcome page with setup wizard.
3. Use the credential helper to either upload the Google JSON or point at an existing path that already lives on the machine.
4. Paste review and published sheet IDs from Google Sheets.
5. Select embedding model size (auto-download on first use).
6. Click "Initialize" to create database, download models, and start workers.

**Daily Operations**
1. Navigate to Import page, click "Import from Published Sheet" button.
2. See progress bar and worker status; workers automatically pause/drain during import.
3. After import completes, workers resume automatically.
4. Navigate to Queue page to monitor embedding progress in real-time.
5. Navigate to Export page, review pending entries count, click "Export to Review Sheet".

**Configuration Changes**
1. Navigate to Settings page to update sheet IDs, credentials, or model choices.
2. Changes persist to database and take effect immediately (or after restart prompt for model changes).
3. View current configuration and validation status (credentials valid, sheets accessible, models loaded).
4. Use the diagnostics/test action plus audit/backup cards to verify connectivity, download metadata, or restore a previous snapshot without editing files manually.

## Operational Considerations
- UI must enforce safe operations: disable import button while workers are running, show confirmation for destructive actions.
- Server-side endpoints mirror those guarantees by acquiring advisory locks before running long tasks (import/export/index rebuild) and rejecting concurrent or invalid requests even if they originate outside the browser UI.
- File uploads (credentials, index files) need size limits and validation before processing.
- Credential helpers either copy uploaded files into a managed directory (e.g., `~/.config/chl/credentials/`) or register a pre-existing path on disk; in both cases SQLite stores only the path, checksum, and last validation timestamp so sensitive JSON never lives in the database.
- Long-running operations (import, export, index rebuild) should show progress and allow cancellation where safe.
- Error messages must be user-friendly and actionable (not stack traces or technical jargon).
- Configuration changes should validate before saving (test sheet access, verify credentials format).
- Provide export/download for configuration and logs so users can backup or share setup details.
- Keep the API server single-process for simplicity; horizontal scaling is not a near-term concern.
- The operations dashboard streams queue/worker/job updates over SSE (`/ui/stream/telemetry`) while htmx events (`ops-refresh`) provide an immediate fallback when users trigger actions.

## Delivery Plan
- **Phase 0 - API Foundations**: Introduce settings/import/export/worker-control endpoints plus locking/validation so both CLI and UI clients can rely on the HTTP surface.
- **Phase 1 - MCP HTTP Client**: Refactor MCP server to call the new APIs, guarded by a feature flag fallback to direct database mode until parity is confirmed.
- **Phase 2 - Settings & Configuration UI**: Interactive settings page with credential placement helper (upload into managed directory or point at an existing local file), sheet/model forms, diagnostics panel, audit log feed, and metadata backup/restore. Store metadata in SQLite while secrets stay on disk; secrets remain files under the managed root.
- **Phase 3 - Core Operations & UX**: Import/export pages, worker control dashboard, queue monitoring backed by the telemetry pipeline, and user-experience polish such as real-time visualizations, progress indicators, validation feedback, and initial mobile responsiveness.

## MCP HTTP Rollout Controls
- `CHL_MCP_HTTP_MODE` governs transport: `http` (force API), `auto` (API with direct fallback), or `direct` (legacy SQLite). Legacy `CHL_USE_API=0` maps to `direct`.
- `--chl-http-mode` CLI flag temporarily overrides the environment, making it easy to test a mode without editing config files.
- HTTP startup waits for `/health` and uses `CHL_API_CIRCUIT_BREAKER_*` thresholds so outages trip a circuit breaker instead of hanging MCP calls. Auto mode transparently drops to direct handlers when transport errors occur.
- Categories payloads fetched over HTTP are cached for 30 seconds; handshake responses include the currently active transport so clients can display status.
- Set `CHL_SKIP_MCP_AUTOSTART=1` in tests to import `src.server` without immediately starting the HTTP client, enabling deterministic injection of fake API clients.


## Technology Choices
**UI Framework: Jinja2 + htmx**
- Use Jinja2 templates (built into FastAPI) for server-side rendering of HTML structure, forms, and layouts.
- Use htmx for interactive behavior without page reloads (inline editing, partial updates, live previews).
- This combination is essential for editing database content (experiences and manuals) with good UX.
- Jinja2 handles complex forms with validation and renders data from SQLite.
- htmx enables modern interactions (save without reload, live markdown preview, inline editing) without JavaScript frameworks or build steps.
- Together they provide traditional server-side simplicity with modern user experience.
- Minimal vanilla JavaScript only for nice-to-have features (syntax highlighting, auto-resize textareas).

**Why not React/Vue**
- Requires build pipeline and adds complexity for simple CRUD operations.
- Duplicates validation logic between client and server.
- Overkill for primarily form-based interactions and administrative operations.
- Can migrate later if UI interactions become significantly more complex.

**Styling**
- Use lightweight CSS framework (PicoCSS or Tailwind) for consistent look without custom design work.
- Prioritize functional clarity over visual polish in initial versions.
- Ensure forms and tables are readable and accessible without heavy customization.

**Real-time Updates**
- Server-sent events for one-way updates (queue status, worker metrics, progress indicators).
- htmx supports SSE natively for updating page sections as events arrive; FastAPI streams JSON generated from worker heartbeats and queue-length samples.
- Avoid WebSocket complexity unless bidirectional communication becomes necessary.

## Risks & Mitigations
- **Browser compatibility issues** → Test on major browsers (Chrome, Firefox, Safari) and document minimum versions.
- **File upload security** → Validate file types, enforce size limits, scan for malicious content before processing.
- **Configuration corruption** → Backup settings before applying changes; provide reset-to-defaults option.
- **MCP performance regression** → Benchmark HTTP client overhead; optimize hot paths or maintain hybrid mode if needed.
- **User confusion during migration** → Provide clear migration guide and support both configuration methods during transition.
- **API server becomes single point of failure** → Document restart procedures and provide health monitoring.
