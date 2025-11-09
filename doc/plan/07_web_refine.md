# Web Interface Refinement - UX Simplification

**STATUS: PLANNING DOCUMENT - NOT YET IMPLEMENTED**

This document describes planned improvements to the web interface and configuration workflow. For current usage, see the existing README and documentation. Implementation will be tracked in separate issues/PRs. Refer to `doc/plan/07_plan_review.md` (updated 2025-11-09) for the latest gap analysis against the live system.

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

## Implementation Roadmap

**IMPORTANT:** The following phases represent planned work. None of these phases are currently implemented. Track progress via GitHub issues/PRs.

## Interim Guidance (Until Phases Ship)
- Keep following the README’s YAML-driven onboarding flow (`scripts/scripts_config.yaml` + Settings forms) so operators can finish setup today.
- Document the manual FAISS workflow (Run Import → Rebuild Index → Upload/Download snapshots) in user-facing guides until Phases 3–4 remove the Rebuild button.
- Treat `.env` variables as future enhancements only; do not remove existing environment overrides or MCP client env sections until python-dotenv is live.
- Any UI or docs changes must highlight that automation is still pending to avoid confusing current users.

## Implementation Phases

### Phase 1: Add python-dotenv and Migrate to .env Configuration

**Status:** ❌ NOT STARTED

**Acceptance Criteria:**
- [ ] python-dotenv added to pyproject.toml dependencies
- [ ] `.env.sample` created in project root with documented variables
- [ ] `.env` and `.env.local` added to .gitignore
- [ ] `src/config.py` updated with `load_dotenv()` call before Config class
- [ ] Environment variables `GOOGLE_CREDENTIAL_PATH`, `IMPORT_SPREADSHEET_ID`, `EXPORT_SPREADSHEET_ID` supported
- [ ] `scripts/setup.py` enhanced to:
  - Read `GOOGLE_CREDENTIAL_PATH` from environment
  - Copy credential JSON to `data/credentials/service-account.json`
  - Set chmod 600 on copied credential file
  - Validate JSON structure and test Google Sheets connection
- [ ] `scripts/import.py` and `scripts/export.py` updated to read sheet IDs from environment (priority: env > YAML)
- [ ] README updated to document `.env` workflow
- [ ] Backward compatibility: existing `scripts_config.yaml` workflow still works as fallback

**Tasks:**
- Add python-dotenv dependency to pyproject.toml
- Create .env.sample with all required environment variables
- Update src/config.py to auto-load .env at module level
- Enhance setup.py for credential handling and validation
- Update import.py/export.py to prioritize environment variables
- Update README and documentation

### Phase 2: Simplify Settings Page to Diagnostics Only

**Status:** ❌ NOT STARTED
**Depends on:** Phase 1

**Acceptance Criteria:**
- [ ] Credential upload UI removed from Settings page
- [ ] Sheet ID input forms removed from Settings page
- [ ] Model selection UI removed from Settings page (moved to Operations in Phase 3)
- [ ] Configuration status card added (read-only display):
  - [ ] Shows GOOGLE_CREDENTIAL_PATH status (exists, permissions, validity)
  - [ ] Shows IMPORT_SPREADSHEET_ID and EXPORT_SPREADSHEET_ID
  - [ ] Shows worksheet names from config
  - [ ] Displays note: "To change, edit .env file (no restart needed)"
- [ ] "Test Connection" button retained and functional
- [ ] System diagnostics card shows database/models/FAISS/disk status
- [ ] Audit log viewer retained
- [ ] JSON backup/restore functionality retained

**Tasks:**
- Remove `src/web/templates/partials/settings_onboarding.html` interactive forms
- Remove credential upload from `src/web/templates/partials/sheets_card.html`
- Remove model selection from `src/web/templates/partials/models_card.html`
- Add read-only configuration status display reading from environment
- Update Settings service to read config from .env for display
- Update UI tests to match new read-only behavior

### Phase 3: Enhance Operations Page

**Status:** ❌ NOT STARTED
**Depends on:** Phase 1, Phase 2

**Acceptance Criteria:**
- [ ] Import/Export cards display current configuration:
  - [ ] Show sheet IDs from environment
  - [ ] Show worksheet names
  - [ ] Display note: "To change, edit .env (no restart needed)"
- [ ] "Model Management" card added to Operations page:
  - [ ] Shows current embedding and reranker models
  - [ ] "Change Models" button opens selection modal
  - [ ] Model selection shows size/VRAM requirements
  - [ ] Download progress indicator for new models
  - [ ] Re-embedding impact warning (item counts, estimated time)
  - [ ] "Download & Re-embed Now" or "Cancel" options
- [ ] Model selection UI removed from Settings page
- [ ] "Rebuild Index" button hidden from main UI (kept as admin endpoint)
- [ ] Progress indicators added for long operations

**Tasks:**
- Update `src/web/templates/partials/ops_operations_card.html` to show config
- Create new "Model Management" card template
- Implement model selection modal with download workflow
- Move model picker from Settings to Operations
- Hide Rebuild Index button (keep /ui/operations/run/index as hidden endpoint)
- Add progress indicators to import/export cards

### Phase 4: Automatic Background Processing

**Status:** ❌ NOT STARTED  
**Depends on:** Phase 1, Phase 2, Phase 3

**Acceptance Criteria:**
- [ ] Import jobs enqueue embedding work automatically (no manual intervention)
- [ ] Background worker processes pending embeddings and updates FAISS incrementally
- [ ] Automatic index rebuild triggers only when thresholds require it (model change, corruption, admin override)
- [ ] UI telemetry surfaces job progress (SSE) for embedding/index tasks
- [ ] Model hot-reload path avoids server restarts after embeddings regenerate
- [ ] Failure cases (worker offline, FAISS write error) surface actionable alerts in UI and logs

**Tasks:**
- Extend operations service to chain embedding/index jobs when import/model operations finish
- Implement worker/job queue capable of incremental embedding + FAISS updates
- Wire telemetry + diagnostics to show background job backlog and status
- Add guardrails (timeouts, retries, alerting) for embedding/index automation
- Update documentation to describe the hands-off workflow

### Phase 5: Model Change Workflow

**Status:** ❌ NOT STARTED  
**Depends on:** Phase 3, Phase 4

**Acceptance Criteria:**
- [ ] “Change Models” modal supports choosing new embedding/reranker bundles with size/VRAM guidance
- [ ] Download progress, checksum validation, and caching feedback presented to the user
- [ ] Selecting a new model automatically triggers full re-embedding + FAISS rebuild with ETA and cancellation guardrails
- [ ] Disk space, permission, or download failures bubble up with remediation steps
- [ ] Operations history and audit log capture model change events with actor + context
- [ ] MCP/search components observe the new models without manual restarts

**Tasks:**
- Build modal + backend endpoint for model selection and download orchestration
- Integrate with background processing pipeline to kick off re-embedding safely
- Implement progress + cancellation hooks exposed over SSE/Web UI
- Persist model selection outcomes and link them to `data/model_selection.json`
- Update docs/runbooks to reflect the new self-service model workflow

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
