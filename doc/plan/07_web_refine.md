# Web Interface Refinement - UX Simplification

**STATUS: ✅ COMPLETED (All Phases 1-5 Implemented)**

This document describes the web interface and configuration workflow improvements that have been implemented. All 5 phases are now complete:
- Phase 1: ✅ python-dotenv and .env configuration
- Phase 2: ✅ Settings page simplified to diagnostics only
- Phase 3: ✅ Enhanced Operations page with model management
- Phase 4: ✅ Automatic background processing and embedding workflow
- Phase 5: ✅ Model change workflow with automatic re-embedding

## Purpose
- Clarify the separation between first-time setup (CLI) and ongoing operations (Web UI)
- Remove redundant manual index rebuild operations from user-facing workflows
- Automate embedding generation and index management as background processes
- Improve model change experience with clear re-indexing workflows
- Migrate from YAML-based configuration to industry-standard `.env` file pattern

## Current State (As-Is)

**Configuration:**
- Credentials and sheet IDs configured via `scripts/scripts_config.yaml`
- No `.env` file or python-dotenv integration
- Settings UI has interactive forms for credentials, sheet IDs, and model selection
- `/settings` onboarding checklist instructs operators to copy/edit `scripts_config.yaml` and run "Load & Verify"
- MCP client requires explicit env section in mcp.json to override defaults

**Operations:**
- Import clears embeddings and marks them as "pending"
- User must manually click "Rebuild Index" after import
- `/operations` still exposes explicit Import/Export/Rebuild buttons backed by CLI scripts
- No automatic embedding generation
- Model changes require manual server restart and index rebuild

**Pain Points:**
- Configuration scattered between `scripts_config.yaml` and web UI forms
- Credentials and sheet IDs mixed with structural configuration in YAML
- "Rebuild Index" button exposes internal implementation details to users
- No automatic embedding generation during import/export operations
- Users must understand FAISS internals to use the system effectively
- MCP client configuration duplicates environment variables from config files

## Proposed User Journey (Target State)

### First-Time Setup (CLI - Before Server Start)
User prepares `.env` file for secrets and environment-specific configuration:
- Copy `.env.sample` to `.env`
- Edit environment variables:
  - `GOOGLE_CREDENTIAL_PATH` - Path to service account JSON (e.g., `data/credentials/service-account.json`)
  - `IMPORT_SPREADSHEET_ID` - Published spreadsheet ID for imports
  - `EXPORT_SPREADSHEET_ID` - Review spreadsheet ID for exports
  - Optional: Override default worksheet names if needed

User runs `python scripts/setup.py` which:
- Loads `.env` file automatically via python-dotenv
- Checks platform compatibility
- Creates directory structure (`data/`, `data/faiss_index`, `data/credentials`)
- Copies Google credentials from `GOOGLE_CREDENTIAL_PATH` to `data/credentials/service-account.json` with chmod 600
- Validates credential JSON structure and permissions
- Tests Google Sheets API connection (optional)
- Initializes SQLite database with schema
- Seeds default categories and sample experiences
- Provides interactive model selection (0.6B/4B/8B for embedding and reranker)
- Downloads selected models to `~/.cache/huggingface/`
- Creates `data/model_selection.json` to persist choices
- Validates complete setup
- Prints clear next steps (start server, run import)

Note: `scripts/scripts_config.yaml` remains for structural defaults (worksheet names, data paths) that rarely change.

### Settings Dashboard (Web - System Status & Diagnostics)
Simplified to read-only status display and testing:

**Configuration Status (Read-Only):**
- Display credential status: "✓ Found at data/credentials/service-account.json"
- Display import sheet: "1abc...xyz (worksheets: Categories, Experiences, Manuals)"
- Display export sheet: "1def...uvw (worksheets: Categories, Experiences, Manuals)"
- Note: "To change credentials or sheet IDs, edit .env file (no restart needed)"

**Connection Testing:**
- "Test Connection" button to validate credentials and list worksheets
- Shows success/error with actionable guidance

**System Diagnostics:**
- Database status (path, size, record counts)
- Models loaded (embedding and reranker with versions)
- FAISS index status (vectors, dimension, last updated)
- Disk space available

**Audit Log Viewer:**
- Recent operations and configuration changes

**Data Backup/Restore:**
- JSON export/import for experiences and manuals

**Removed from Settings:**
- Credential upload forms (handled by setup.py)
- Sheet ID input forms (handled by setup.py)
- Model selection UI (moved to Operations)
- First-time setup instructions (now in setup.py output)

### Operations Dashboard (Web - Day-to-Day Management)
Enhanced to handle model management and automated workflows:

**Data Operations:**
- Import Card:
  - Display current source: "Sheet 1abc...xyz / Experiences"
  - [Run Import] button (automatically generates embeddings for all imported content)
  - Progress indicator during operation
  - Note: "To change source sheet, edit .env (no restart needed)"
- Export Card:
  - Display current target: "Sheet 1def...uvw / Experiences"
  - [Run Export] button (data only, embeddings remain untouched)
  - Progress indicator during operation
  - Note: "To change target sheet, edit .env (no restart needed)"

**Model Management (new section):**
- Display current active models with repo and quantization details
- "Change Models" button that:
  - Shows model selection interface with size/VRAM requirements
  - Downloads new models if not cached (with progress)
  - Warns about re-indexing impact (e.g., "14 experiences, 10 manuals, ~5 minutes")
  - Offers "Download & Re-embed Now" or "Cancel"
  - Automatically re-generates all embeddings with new model
  - Updates FAISS index without manual intervention

**Index Status (informational):**
- Current index statistics (item count, last updated timestamp)
- FAISS snapshot upload/download for backup/restore

**Job Monitoring:**
- Live queue depth and job progress via SSE
- Job history with status and logs
- Worker status (if worker pool is configured)

**Removed from Operations:**
- "Rebuild Index" button (now automatic background process)

## Automatic Embedding & Index Management

### Trigger Points for Automatic Embedding Generation
1. **Import Operation**: All new or updated experiences/manuals get embeddings generated automatically
2. **Model Change**: User-initiated re-embedding of all content with new model
3. **Create/Update via API**: Single item embedding generation on content changes
4. **Background Processing**: Pending embeddings processed by background worker

### FAISS Index Update Strategy
- **Incremental Updates**: Single item changes update index in-place (preferred for performance)
- **Full Rebuild**: Only when necessary (model change, index corruption, manual admin request)
- **Automatic**: Users never need to manually rebuild unless recovering from errors

### User Feedback During Operations
- Progress indicators: "Generating embeddings: 8/24 (33%)"
- Estimated time remaining for long operations
- Clear success/error messages with actionable guidance
- Cancellation support for long-running tasks

## .env File Structure

The `.env` file contains all secrets and environment-specific configuration:

```bash
# ==============================================================================
# CHL Environment Configuration
# ==============================================================================
# Copy this file to .env and fill in your values
# .env is gitignored and should never be committed

# ------------------------------------------------------------------------------
# Google Sheets Integration (Required)
# ------------------------------------------------------------------------------
# Path to Google service account JSON credential file
# Relative paths resolve from project root
GOOGLE_CREDENTIAL_PATH=data/credentials/service-account.json

# Published spreadsheet ID (source for imports)
IMPORT_SPREADSHEET_ID=your-published-sheet-id-here

# Review spreadsheet ID (target for exports)
EXPORT_SPREADSHEET_ID=your-review-sheet-id-here

# ------------------------------------------------------------------------------
# Google Sheets Worksheet Names (Optional - Override Defaults)
# ------------------------------------------------------------------------------
# Uncomment to customize if your sheets don't use default tab names
# IMPORT_WORKSHEET_CATEGORIES=Categories
# IMPORT_WORKSHEET_EXPERIENCES=Experiences
# IMPORT_WORKSHEET_MANUALS=Manuals
# EXPORT_WORKSHEET_CATEGORIES=Categories
# EXPORT_WORKSHEET_EXPERIENCES=Experiences
# EXPORT_WORKSHEET_MANUALS=Manuals
```

**What stays in scripts_config.yaml:**
- Structural defaults (default worksheet names: "Categories", "Experiences", "Manuals")
- Path configuration (data_path, database_filename)
- Script behavior settings (dry_run, verbose flags)

**Separation of concerns:**
- `.env` = Secrets + environment-specific (credentials, sheet IDs)
- `scripts_config.yaml` = Structure + defaults (worksheet names, paths)
- User only needs to edit `.env` for basic operation

## Updated README Structure
1. Install uv
2. Clone repository and sync dependencies
3. **Configure environment** (copy `.env.sample` to `.env`, fill in values) ← NEW STEP
4. **Run first-time setup** (`python scripts/setup.py`) ← NEW STEP
5. Start server (automatically loads `.env`)
6. Run Import at `/operations` (embeddings generated automatically)
7. Start using search immediately

Note: All secrets and environment-specific configuration live in `.env` file. The `.env` file is auto-loaded by both the FastAPI server and MCP server via python-dotenv. No web UI configuration or MCP client env section needed.

## Implementation Summary

All phases have been successfully implemented. The system now provides:
- ✅ Standard `.env` configuration with python-dotenv auto-loading
- ✅ Simplified Settings page (diagnostics only, no configuration forms)
- ✅ Enhanced Operations page with model management UI
- ✅ Automatic background embedding processing with SSE telemetry
- ✅ Self-service model changes with automatic re-embedding

**Current User Workflow:**
1. Configure `.env` file with credentials and sheet IDs
2. Run `python scripts/setup.py` for first-time initialization
3. Start server with `uv run python -m src.api_server`
4. Use Operations page for day-to-day management (import, export, model changes)
5. All embedding generation and index updates happen automatically in background

## Implementation Phases

### Phase 1: Add python-dotenv and Migrate to .env Configuration

**Status:** ✅ COMPLETED

**Acceptance Criteria:**
- [x] python-dotenv added to pyproject.toml dependencies
- [x] `.env.sample` created in project root with documented variables
- [x] `.env` and `.env.local` added to .gitignore
- [x] `src/config.py` updated with `load_dotenv()` call before Config class
- [x] Environment variables `GOOGLE_CREDENTIAL_PATH`, `IMPORT_SPREADSHEET_ID`, `EXPORT_SPREADSHEET_ID` supported
- [x] `scripts/setup.py` enhanced to:
  - Read `GOOGLE_CREDENTIAL_PATH` from environment
  - Copy credential JSON to `data/credentials/service-account.json`
  - Set chmod 600 on copied credential file
  - Validate JSON structure and test Google Sheets connection
- [x] `scripts/import.py` and `scripts/export.py` updated to read sheet IDs from environment (priority: env > YAML)
- [x] README updated to document `.env` workflow
- [x] Backward compatibility: existing `scripts_config.yaml` workflow still works as fallback

**Tasks:**
- Add python-dotenv dependency to pyproject.toml
- Create .env.sample with all required environment variables
- Update src/config.py to auto-load .env at module level
- Enhance setup.py for credential handling and validation
- Update import.py/export.py to prioritize environment variables
- Update README and documentation

### Phase 2: Simplify Settings Page to Diagnostics Only

**Status:** ✅ COMPLETED
**Depends on:** Phase 1

**Acceptance Criteria:**
- [x] Credential upload UI removed from Settings page
- [x] Sheet ID input forms removed from Settings page
- [x] Model selection UI removed from Settings page (moved to Operations in Phase 3)
- [x] Configuration status card added (read-only display):
  - [x] Shows GOOGLE_CREDENTIAL_PATH status (exists, permissions, validity)
  - [x] Shows IMPORT_SPREADSHEET_ID and EXPORT_SPREADSHEET_ID
  - [x] Shows worksheet names from config
  - [x] Displays note: "To change, edit .env file (no restart needed)"
- [x] "Test Connection" button retained and functional
- [x] System diagnostics card shows database/models/FAISS/disk status
- [x] Audit log viewer retained
- [x] JSON backup/restore functionality retained

**Tasks:**
- Remove `src/web/templates/partials/settings_onboarding.html` interactive forms
- Remove credential upload from `src/web/templates/partials/sheets_card.html`
- Remove model selection from `src/web/templates/partials/models_card.html`
- Add read-only configuration status display reading from environment
- Update Settings service to read config from .env for display
- Update UI tests to match new read-only behavior

### Phase 3: Enhance Operations Page

**Status:** ✅ COMPLETED
**Depends on:** Phase 1, Phase 2

**Acceptance Criteria:**
- [x] Import/Export cards display current configuration:
  - [x] Show sheet IDs from environment
  - [x] Show worksheet names
  - [x] Display note: "To change, edit .env (no restart needed)"
- [x] "Model Management" card added to Operations page:
  - [x] Shows current embedding and reranker models
  - [x] "Change Models" button opens selection modal
  - [x] Model selection shows size/VRAM requirements
  - [x] Download progress indicator for new models (UI ready, backend in Phase 4)
  - [x] Re-embedding impact warning (item counts, estimated time)
  - [x] "Download & Re-embed Now" or "Cancel" options
- [x] Model selection UI removed from Settings page (completed in Phase 2)
- [x] "Rebuild Index" button hidden from main UI (kept as admin endpoint)
- [x] Progress indicators added for long operations (UI ready, backend in Phase 4)

**Tasks:**
- Update `src/web/templates/partials/ops_operations_card.html` to show config
- Create new "Model Management" card template
- Implement model selection modal with download workflow
- Move model picker from Settings to Operations
- Hide Rebuild Index button (keep /ui/operations/run/index as hidden endpoint)
- Add progress indicators to import/export cards

### Phase 4: Automatic Background Processing

**Status:** ✅ COMPLETED
**Depends on:** Phase 1, Phase 2, Phase 3

**Acceptance Criteria:**
- [x] Import jobs enqueue embedding work automatically (no manual intervention)
  - Import handler automatically triggers sync job on success (operations_service.py:305-310)
  - Sync handler runs sync_embeddings.py + rebuild_index.py sequentially
- [x] Background worker processes pending embeddings and updates FAISS incrementally
  - BackgroundEmbeddingWorker runs in daemon thread with configurable poll interval
  - Worker processes pending embeddings in batches (default: 10 items, 5s interval)
  - Uses leader election via OperationLock to ensure single active worker across processes
  - Incremental FAISS updates via ThreadSafeFAISSManager with configurable save policies
- [x] Automatic index rebuild triggers only when thresholds require it (model change, corruption, admin override)
  - Sync handler triggers rebuild_index.py after successful embedding sync
  - FAISS rebuild threshold configurable via CHL_FAISS_REBUILD_THRESHOLD (default: 0.10)
  - Manual rebuild available via admin endpoint (hidden from main UI)
- [x] UI telemetry surfaces job progress (SSE) for embedding/index tasks
  - SSE endpoint at /ui/stream/telemetry streams real-time updates every 2s
  - Updates queue depth, worker status, job history, and index status
  - Live refresh of all operations cards without page reload
- [x] Model hot-reload path avoids server restarts after embeddings regenerate
  - _hot_reload_faiss_index() function reloads FAISS index without restart
  - ThreadSafeFAISSManager handles concurrent access during reload
  - Upload snapshot endpoint triggers hot-reload automatically
- [x] Failure cases (worker offline, FAISS write error) surface actionable alerts in UI and logs
  - Worker errors logged with full exception details
  - Failed embeddings tracked in database with status='failed'
  - SQLite lock retry with exponential backoff (up to 8 retries)
  - Worker pool status exposed via telemetry and diagnostics UI

**Implementation Details:**
- `src/services/background_worker.py` - Background worker with leader election and statistics
- `src/services/operations_service.py` - Auto-trigger sync after import (lines 302-312)
- `src/api_server.py` - Worker initialization and lifecycle management (lines 204-245)
- `src/services/telemetry_service.py` - Real-time telemetry collection
- `src/api/routers/ui.py` - SSE stream endpoint for live updates (lines 1530-1589)
- `src/search/thread_safe_faiss.py` - Thread-safe FAISS operations with save policies

**Configuration:**
- `CHL_WORKER_AUTO_START` - Auto-start worker on server startup (default: 1)
- `CHL_WORKER_POLL_INTERVAL` - Worker poll interval in seconds (default: 5.0)
- `CHL_WORKER_BATCH_SIZE` - Max entries per batch (default: 10)
- `CHL_WORKER_LEASE_TTL` - Leader election lease TTL in seconds (default: 30)
- `CHL_FAISS_SAVE_POLICY` - Save policy: immediate, periodic, manual (default: immediate)
- `CHL_FAISS_SAVE_INTERVAL` - Save interval for periodic mode (default: 300s)
- `CHL_FAISS_REBUILD_THRESHOLD` - Tombstone ratio for auto-rebuild (default: 0.10)

**Tasks:**
- [x] Extend operations service to chain embedding/index jobs when import/model operations finish
- [x] Implement worker/job queue capable of incremental embedding + FAISS updates
- [x] Wire telemetry + diagnostics to show background job backlog and status
- [x] Add guardrails (timeouts, retries, alerting) for embedding/index automation
- [x] Update documentation to describe the hands-off workflow

### Phase 5: Model Change Workflow

**Status:** ✅ COMPLETED (Server restart required for model loading)
**Depends on:** Phase 3, Phase 4

**Acceptance Criteria:**
- [x] "Change Models" modal supports choosing new embedding/reranker bundles with size/VRAM guidance
  - Modal implemented with dropdown selectors for embedding and reranker models
  - Shows size/VRAM requirements for each option (0.6B Q4_K_M minimum, 4B Q4_K_M recommended)
  - Impact estimate displays experience/manual counts and estimated re-embedding time
- [x] Disk space pre-checks before model changes
  - Pre-flight disk space validation (minimum 10 GB free)
  - Clear error messages if insufficient space
  - Checks home directory where HuggingFace models are cached
- [x] Selecting a new model automatically triggers full re-embedding + FAISS rebuild with ETA and cancellation guardrails
  - Model selection saves to `data/model_selection.json`
  - Automatic re-embedding trigger via `reembed` operation handler
  - Reembed handler marks all entities as 'pending' and triggers sync job
  - Cancellation support via existing `/ui/operations/jobs/{job_id}/cancel` endpoint
- [x] Operations history and audit log capture model change events with actor + context
  - Model changes logged to audit_log table with event_type='models.changed'
  - Audit log includes full model selection payload (repos, quantizations)
- [x] MCP/search components observe the new models after server restart
  - Models reloaded from model_selection.json on server startup
  - Hot-reload module implemented (`src/embedding/hot_reload.py`) for future enhancement
  - Current workflow: Change models → Restart server → Auto re-embedding begins

**Implementation Details:**
- `src/services/operations_service.py` - `_reembed_handler()` marks all entities as pending (lines 401-452)
- `src/api/routers/ui.py` - Model change endpoint with disk space checks (lines 969-1070)
- `src/api/routers/ui.py` - Disk space validation helper `_check_disk_space()` (lines 969-991)
- `src/embedding/hot_reload.py` - Hot-reload utilities for embedding/reranker clients
- Job cancellation via `OperationsService.cancel_job()` and UI endpoint

**Configuration:**
- Disk space requirement: 10 GB minimum (configurable in `_check_disk_space()`)
- Models cached in `~/.cache/huggingface/` (HuggingFace Hub default)
- Model selection persisted in `data/model_selection.json`

**Known Limitations:**
- **Server restart required**: New models are loaded on server startup, not hot-reloaded
  - Rationale: llama-cpp-python loads models into memory at initialization time
  - Hot-reload module provided for future enhancement if llama-cpp adds support
- **Model download progress**: Progress tracking not implemented
  - Model downloads happen during EmbeddingClient/RerankerClient initialization
  - Would require hooks into huggingface_hub's download mechanism
  - Downloads show in server logs but not in UI

**User Workflow:**
1. User selects new models via Operations page "Change Models" button
2. System validates disk space (10 GB minimum)
3. Model selection saved to `data/model_selection.json`
4. Reembed job queued (all entities marked as 'pending')
5. User restarts server to load new models
6. Background worker processes pending embeddings automatically
7. FAISS index rebuilt automatically via sync job

**Tasks:**
- [x] Build modal + backend endpoint for model selection and download orchestration
- [x] Integrate with background processing pipeline to kick off re-embedding safely
- [x] Implement cancellation hooks via existing operations service
- [x] Persist model selection outcomes and link them to `data/model_selection.json`
- [x] Update docs/runbooks to reflect the new self-service model workflow
- [x] Add disk space pre-checks before model changes
- [x] Implement hot-reload utilities (for future use)

## Technical Notes

### Environment Variable Loading with python-dotenv
All components auto-load `.env` file via python-dotenv for seamless configuration:

**src/config.py (shared by all components):**
```python
from pathlib import Path
from dotenv import load_dotenv

# Auto-load .env from project root (before Config class)
PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env")

class Config:
    # Now os.getenv() finds variables from .env automatically
    def __init__(self):
        self.credential_path = os.getenv("GOOGLE_CREDENTIAL_PATH", ...)
```

**Components that benefit:**
- **FastAPI server** (`uvicorn src.api_server:app`) - Loads .env when importing src.config
- **MCP server** (`uv run python src/server.py`) - Loads .env on startup, no mcp.json env section needed
- **Scripts** (`scripts/import.py`, `scripts/export.py`) - Load .env at runtime via src.config
- **setup.py** - Loads .env to read GOOGLE_CREDENTIAL_PATH

### No Server Restart Needed for Configuration Changes
Import/export operations run as separate Python subprocesses that reload `.env` at runtime:
- Web UI triggers operation → `OperationsService` spawns subprocess
- Subprocess runs `scripts/import.py` or `scripts/export.py` with fresh Python interpreter
- Script imports `src.config` which calls `load_dotenv()` and reads `.env` at that moment
- Changes to `.env` take effect immediately on next operation
- No server restart required for credential or sheet ID changes

### Configuration Architecture and Priority
**Configuration hierarchy (highest to lowest priority):**
1. Environment variables from shell/MCP client env section (if explicitly set)
2. Variables from `.env` file (auto-loaded via python-dotenv)
3. Values from `scripts_config.yaml` (structural defaults, worksheet names)
4. Hardcoded defaults in src/config.py

**File purposes:**
- `.env` - Secrets and environment-specific config (credentials, sheet IDs) - **gitignored**
- `scripts/scripts_config.yaml` - Structural defaults (worksheet names, paths) - **rarely changed**
- `data/model_selection.json` - Model preferences persisted by setup.py - **auto-generated**

**Benefits:**
- Single source of truth for secrets (`.env`)
- Standard Python pattern (python-dotenv)
- No duplication across config files
- Works identically for FastAPI server, MCP server, and scripts

## Success Criteria
- New users can go from clone to working system in 6 steps without debugging
- All secrets and environment-specific configuration in standard `.env` file
- `.env` auto-loaded by all components (FastAPI, MCP server, scripts) via python-dotenv
- No duplication of credentials between `.env` and MCP client configuration
- Import operation automatically makes content searchable without manual index rebuild
- Configuration changes (credentials, sheet IDs) take effect immediately without restart
- Model changes are self-service operations through the web UI
- Users never see "Rebuild Index" button or need to understand FAISS internals
- Settings page is purely diagnostic/informational (no configuration forms)
- Zero server restarts required for configuration or model changes
- MCP client configuration requires no env section (reads from `.env` automatically)
