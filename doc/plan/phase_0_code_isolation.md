# Phase 0: Codebase Isolation Implementation Plan (v2)

## Overview
Restructure `src/` to cleanly separate API server, MCP server, and shared components, while isolating CPU-specific and GPU-specific implementations within the API server.

**Migration Strategy**: Full cutover, no backward compatibility, all imports updated explicitly.

**Testing Strategy**: CPU → GPU → MCP (validate at each checkpoint before proceeding)

## Goals
1. **API ↔ MCP Separation**: Eliminate accidental shared state between servers
2. **CPU ↔ GPU Isolation**: Separate implementations using strategy patterns
3. **Clear Boundaries**: Enforce module boundaries to prevent coupling
4. **Foundation for Phase A-D**: Enable platform-specific requirements work

## Target Directory Structure

```
src/
├── api/                                    # API server code ONLY
│   ├── cpu/                               # CPU-only implementations
│   │   ├── runtime.py                     # BUILD: Migrate from modes/sqlite_only/runtime.py
│   │   └── search_provider.py             # BUILD: Migrate from search/sqlite_provider.py
│   ├── gpu/                               # GPU-accelerated implementations
│   │   ├── runtime.py                     # BUILD: Migrate from modes/vector/runtime.py
│   │   ├── embedding_client.py            # BUILD: Migrate from embedding/client.py
│   │   ├── embedding_service.py           # BUILD: Migrate from embedding/service.py (GPU-only, 663 lines)
│   │   ├── reranker_client.py             # BUILD: Migrate from embedding/reranker.py
│   │   ├── search_provider.py             # BUILD: Migrate from search/vector_provider.py
│   │   └── faiss_manager.py               # BUILD: Migrate from search/faiss_index.py + thread_safe_faiss.py
│   ├── routers/                           # FastAPI routers
│   │   ├── ui.py                          # UPDATE: Fix imports (src.services → src.api.services, src.api_server → src.api.server)
│   │   ├── categories.py                  # UPDATE: Fix imports (src.storage → src.common.storage)
│   │   ├── entries.py                     # UPDATE: Fix imports
│   │   ├── search.py                      # UPDATE: Fix imports
│   │   ├── guidelines.py                  # UPDATE: Fix imports
│   │   ├── admin.py                       # UPDATE: Fix imports
│   │   ├── settings.py                    # UPDATE: Fix imports
│   │   ├── health.py                      # UPDATE: Fix imports
│   │   ├── operations.py                  # UPDATE: Fix imports
│   │   ├── workers.py                     # UPDATE: Fix imports
│   │   └── telemetry.py                   # UPDATE: Fix imports
│   ├── services/                          # Business services (NOT embedding service)
│   │   ├── search_service.py              # MIGRATE + REFACTOR: Move from search/service.py, use SearchProvider protocol
│   │   ├── settings_service.py            # MIGRATE: Move from services/, update imports
│   │   ├── operations_service.py          # MIGRATE: Move from services/, update imports
│   │   ├── worker_control.py              # MIGRATE: Move from services/, update imports
│   │   ├── background_worker.py           # MIGRATE + REFACTOR: Move from services/, GPU-aware imports
│   │   ├── telemetry_service.py           # MIGRATE: Move from services/, update imports
│   │   ├── telemetry_names.py             # MIGRATE: Move from services/
│   │   └── gpu_installer.py               # MIGRATE: Move from services/
│   ├── templates/                         # Mode-specific web UI (RENAMED from src/web/)
│   │   ├── common/                        # Shared templates across modes
│   │   │   ├── base.html
│   │   │   ├── doc_viewer.html
│   │   │   └── partials/
│   │   │       ├── sidebar.html
│   │   │       ├── flash.html
│   │   │       ├── audit_log.html
│   │   │       ├── config_status_card.html
│   │   │       ├── diagnostics_panel.html
│   │   │       └── sheets_card.html
│   │   ├── cpu/                           # CPU-mode templates
│   │   │   ├── settings_cpu.html
│   │   │   ├── operations_cpu.html
│   │   │   └── partials/
│   │   │       ├── ops_onboarding_cpu.html
│   │   │       └── settings_onboarding_cpu.html
│   │   └── gpu/                           # GPU-mode templates
│   │       ├── settings_gpu.html
│   │       ├── operations_gpu.html
│   │       └── partials/
│   │           ├── ops_onboarding_gpu.html
│   │           ├── settings_gpu_runtime.html
│   │           ├── settings_onboarding_gpu.html
│   │           ├── models_card.html
│   │           ├── ops_models_card.html
│   │           ├── ops_queue_card.html
│   │           ├── ops_operations_card.html
│   │           ├── ops_jobs_card.html
│   │           └── model_change_modal.html
│   ├── runtime_builder.py                 # CREATE: Factory for building CPU/GPU runtimes (extracted from modes/base.py)
│   ├── dependencies.py                    # UPDATE: Fix imports, use runtime_builder
│   ├── metrics.py                         # UPDATE: Fix imports
│   ├── models.py                          # NO CHANGE: API-specific DTOs
│   └── server.py                          # CREATE: Move from api_server.py, implement runtime selection
├── mcp/                                    # MCP server code ONLY
│   ├── errors.py                          # NO CHANGE
│   ├── utils.py                           # UPDATE: Fix imports if needed
│   ├── models.py                          # NO CHANGE: MCP-specific DTOs
│   ├── handlers_entries.py                # UPDATE: Fix imports, use CHLAPIClient from common
│   ├── handlers_guidelines.py             # UPDATE: Fix imports, use CHLAPIClient from common
│   └── server.py                          # CREATE: Move from server.py, update imports
├── common/                                 # Shared infrastructure ONLY
│   ├── api_client/                        # HTTP client for API server (used by scripts, MCP)
│   │   ├── client.py                      # MIGRATE: Move from api_client.py (CHLAPIClient)
│   │   └── errors.py                      # CREATE: Extract CHLAPIError, APIConnectionError, APIOperationError
│   ├── config/                            # Configuration management
│   │   └── config.py                      # MIGRATE: Move from config.py
│   ├── dto/                               # Shared data transfer objects
│   │   └── models.py                      # MIGRATE: MCP-specific DTOs consumed by API routers
│   ├── storage/                           # Database and repositories
│   │   ├── database.py                    # MIGRATE: Move from storage/
│   │   ├── repository.py                  # MIGRATE: Move from storage/
│   │   ├── schema.py                      # MIGRATE: Move from storage/
│   │   └── sheets_client.py               # MIGRATE: Move from storage/
│   ├── interfaces/                        # Abstract interfaces/protocols
│   │   ├── embedding.py                   # CREATE: EmbeddingProvider protocol
│   │   ├── search.py                      # CREATE: SearchProvider protocol + SearchProviderError
│   │   ├── search_models.py               # CREATE: SearchResult, DuplicateCandidate, SearchReason (from search/models.py)
│   │   └── runtime.py                     # CREATE: ModeRuntime dataclass + adapter protocols (ONLY typing.Protocol, NO src.api.* imports)
│   └── web_utils/                         # Shared web rendering utilities (RENAMED from web/)
│       ├── static/                        # MIGRATE: Move from web/static/
│       │   ├── css/
│       │   └── favicon.ico
│       └── docs.py                        # MIGRATE: Move from web/docs.py (markdown rendering)

# DELETED (old structure):
├── modes/                                  # DELETE: Migrated to api/cpu/ and api/gpu/
├── embedding/                              # DELETE: Migrated to api/gpu/
├── search/                                 # DELETE: Migrated to api/cpu/, api/gpu/, and common/interfaces/
├── services/                               # DELETE: Migrated to api/services/
├── storage/                                # DELETE: Migrated to common/storage/
├── api_client.py                           # DELETE: Migrated to common/api_client/client.py
├── config.py                               # DELETE: Migrated to common/config/config.py
├── server.py                               # DELETE: Moved to mcp/server.py
├── api_server.py                           # DELETE: Moved to api/server.py
├── web/                                    # DELETE: Split into common/web_utils/ and api/templates/
└── mcp/api_client.py                       # DELETE: Replaced by common/api_client/client.py (CHLAPIClient)
```

**Key Changes from v1:**
1. **Simplified web structure**: `common/web_utils/` (rendering) + `api/templates/` (UI templates)
2. **Single API client**: Deleted `mcp/api_client.py`, all HTTP communication via `common/api_client/client.py`
3. **No optional modules**: Removed `common/models/domain.py` (use api/mcp DTOs directly)
4. **Scripts strategy documented**: See Step 19 for per-script migration table

## Migration Actions by Component

### Phase 1: Foundation & CPU

**1. Create Directory Structure**
- Create all new directories listed above (no `__init__.py` files)

**2. Define Common Interfaces** (`src/common/interfaces/`)
- `search_models.py`: Migrate `SearchResult`, `DuplicateCandidate`, `SearchReason` from `search/models.py`
- `search.py`: Protocol with `is_available`, `search()`, `find_duplicates()`, `rebuild_index()` + `SearchProviderError` exception
- `embedding.py`: Protocol with `encode()`, `encode_single()`, `embedding_dimension`, `get_model_version()`
- `runtime.py`:
  - **ONLY** the `ModeRuntime` dataclass (from `modes/base.py`, NOT `build_mode_runtime()`)
  - Include `OperationsModeAdapter` and `DiagnosticsModeAdapter` as **typing.Protocol definitions ONLY**
  - **CRITICAL**: These protocols must NOT import from `src.api.*` - they define abstract interfaces that api/cpu/gpu implementations will satisfy
  - Use `typing.Protocol` with abstract methods; no concrete implementations or service imports
- **DTO relocation**: Move the MCP-specific DTOs/utilities currently imported by API routers (`ExperienceWritePayload`, `ManualWritePayload`, `format_validation_error`, `normalize_context`) out of `src/mcp` so `src/api` no longer reaches into the MCP package. They can live under `src/common/dto/models.py` as an API dependency, but **MCP must not import them directly**; MCP talks to the API only via HTTP payloads and `CHLAPIClient`-level wrappers.

**3. Migrate Shared API Client** (`src/common/api_client/`)
- Move `src/api_client.py` → `src/common/api_client/client.py`
- Extract exceptions to `src/common/api_client/errors.py`:
  - `CHLAPIError`, `APIConnectionError`, `APIOperationError`
- Update imports: `from src.api_client import CHLAPIClient` → `from src.common.api_client.client import CHLAPIClient`
  - Current consumers: `scripts/import.py`, `scripts/export.py`, `src/services/operations_service.py`
- Extend the client surface while moving it:
  - Add lightweight `.get()/.post()` helpers plus typed wrappers for `/api/v1/settings`, `/api/v1/operations/*`, `/api/v1/entries/*`, and `/health/metrics` so the scripts migration plan remains accurate.
  - Keep compatibility with existing queue helpers, but **do not** reintroduce the circuit-breaker/tenacity retry stack from `src/mcp/api_client.py`. In the local-only model (ADR-004), `CHLAPIClient` should remain a synchronous, single-shot HTTP client that raises standard HTTP exceptions (404, 500, etc.) to the caller with no automatic retries or circuit breakers; callers handle errors explicitly.
  - Treat `CHLAPIClient` as the **canonical boundary adapter** for calling the API: scripts and MCP should never hand-roll their own HTTP clients or reach into API/server internals; instead, they call `CHLAPIClient` methods that encapsulate the HTTP contract.

**4. Migrate Configuration**
- Move `src/config.py` → `src/common/config/config.py`
- **Audit**: Verify no imports from `src.api.*` or `src.mcp.*` (config must be pure)
- Update all imports: `from src.config import` → `from src.common.config.config import`

**5. Migrate Storage**
- Move `src/storage/*` → `src/common/storage/*`
- Update all imports: `from src.storage` → `from src.common.storage`

**6. Migrate Web Resources**
- Move `src/web/docs.py` → `src/common/web_utils/docs.py`
- Move `src/web/static/*` → `src/common/web_utils/static/*`
- Split `src/web/templates/*`:
  - Shared templates → `src/api/templates/common/` (base.html, doc_viewer.html, partials/sidebar.html, etc.)
  - CPU templates → `src/api/templates/cpu/` (settings_cpu.html, operations_cpu.html, partials/ops_onboarding_cpu.html)
  - GPU templates → `src/api/templates/gpu/` (settings_gpu.html, operations_gpu.html, GPU-specific partials)
- Configure a `Jinja2Templates` factory inside `src/api/server.py` that searches `templates/[mode]/`, then `templates/common/`, and inject it into routers instead of constructing template objects at import time. **Note:** The mode is fixed at startup and does not change at runtime.
- Mount `/static` from `src/common/web_utils/static/` inside `src/api/server.py`; delete the per-module static mounting currently in `src/api_server.py`

**7. Extract CPU Implementation** (`src/api/cpu/`)
- Migrate `modes/sqlite_only/runtime.py` → `api/cpu/runtime.py`
- Migrate `search/sqlite_provider.py` → `api/cpu/search_provider.py`
- Update imports to use `common/interfaces/search_models.py` and `common/interfaces/search.py`

**8. Migrate API Services** (`src/api/services/`)
- Move `src/search/service.py` → `src/api/services/search_service.py` (search orchestrator)
- Move `src/services/settings_service.py` → `src/api/services/settings_service.py`
- Move `src/services/operations_service.py` → `src/api/services/operations_service.py`
- Move `src/services/worker_control.py` → `src/api/services/worker_control.py`
- Move `src/services/background_worker.py` → `src/api/services/background_worker.py`
- Move `src/services/telemetry_service.py` → `src/api/services/telemetry_service.py`
- Move `src/services/telemetry_names.py` → `src/api/services/telemetry_names.py`
- Move `src/services/gpu_installer.py` → `src/api/services/gpu_installer.py`
- Update all imports to use `common/*` and `common/interfaces/*`

**9. Consolidate API Server**
- Move `src/api_server.py` → `src/api/server.py`
- Update `src/api/dependencies.py` for new paths (runtime_builder not yet available, will be added in Phase 2)
- Update all `src/api/routers/*` imports
- Replace router/global imports (`src/api/dependencies.py`, `src/api/routers/ui.py` SSE endpoints, etc.) so everything consumes instances passed through `dependencies.py` instead of importing `src.api_server`
- Move MCP coupling from UI routers to API server: `_invalidate_categories_cache_safe` should be migrated to API server internals, not accessed via `sys.modules["src.server"]`
- Delete `src/api_server.py`

**9a. API Surface Alignment**
- Keep all public routes under `/api/v1/...` and update every script/doc example accordingly (no bare `/entries` or `/settings`). If we want short aliases later, add FastAPI sub-routers that simply 307 to the versioned paths. The only deliberate exception is the top-level `/health` probe, which remains unversioned for simplicity and compatibility with existing tooling.
- Promote script-only workflows into first-class operations endpoints so Phase 0 doesn’t block on “manual DB work”:
  - Add operation handlers for “import from Sheets”, “sync embeddings”, “rebuild index”, and “sync guidelines” that wrap the existing script logic (ideally by delegating through `OperationsService` job types). `POST /api/v1/operations/{job}` already exists—ensure job names (`sync-embeddings`, `rebuild-index`, `sync-guidelines`, `import-sheets`) are wired up server-side before scripts switch to HTTP.
  - Expose a read-only `/api/v1/search/health` JSON endpoint that surfaces the data currently produced by `scripts/search_health.py` (counts, FAISS status, warnings) so diagnostics stay available without shelling into SQLite.
- Make sure `SettingsService.snapshot()` is reachable via the versioned API (`GET /api/v1/settings/` already exists); document that these responses include `search_mode`, eliminating the need for scripts to peek into config files.

**10. TEST CPU MODE** ✅ **CHECKPOINT 1**
- Set `CHL_SEARCH_MODE=cpu`
- Start: `python -m src.api.server`
- Verify all endpoints, web UI, SQLite search work
- **DO NOT PROCEED until CPU mode fully validated**

### Phase 2: GPU

**11. Extract GPU Implementation** (`src/api/gpu/`)
- Migrate `embedding/client.py` → `api/gpu/embedding_client.py`
- Migrate `embedding/service.py` → `api/gpu/embedding_service.py` (GPU-only, 663 lines)
- Migrate `embedding/reranker.py` → `api/gpu/reranker_client.py`
- Migrate `search/faiss_index.py` + `search/thread_safe_faiss.py` → `api/gpu/faiss_manager.py`
- Migrate `search/vector_provider.py` → `api/gpu/search_provider.py`
- Migrate `modes/vector/runtime.py` → `api/gpu/runtime.py`
- Update imports to use `common/interfaces/*`

**12. Update Services for GPU**
- Update `background_worker.py`: Import `api/gpu/embedding_service.py` conditionally (GPU mode only)
- Update `search_service.py`: Already protocol-based, should work with GPU provider
- No changes to other services (GPU isolation complete)

**13. Create Runtime Builder** (`src/api/runtime_builder.py`)
- Extract `build_mode_runtime()` function from `modes/base.py`
- Import from both `api/cpu/runtime.py` and `api/gpu/runtime.py`
- This avoids `common → api` boundary violation
- **Note:** This step was moved after GPU implementation to avoid import errors during CPU-only testing

**14. Integrate GPU Runtime**
- Update `src/api/dependencies.py` to use `runtime_builder.py`
- Update routers if needed for GPU templates
- Verify template loader works for GPU mode

**15. TEST GPU MODE** ✅ **CHECKPOINT 2**
- Set `CHL_SEARCH_MODE=gpu`
- Start: `python -m src.api.server`
- Verify FAISS, embeddings, reranking, background worker, web UI
- **DO NOT PROCEED until GPU mode fully validated**

### Phase 3: MCP

**16. Isolate MCP Server**
- Move `src/server.py` → `src/mcp/server.py`
- **Delete `src/mcp/api_client.py`** - replaced by `CHLAPIClient` from `common/api_client/client.py`
- **Audit boundary rules**:
  - MCP must **not** import from `src.api.*` (verify via boundary tests).
  - MCP may only import from these `src.common.*` modules:
    - `src.common.config.config.Config`
    - `src.common.api_client.client.CHLAPIClient`
    - `src.common.dto.*` (shared DTOs for request/response validation)
  - **Critical**: MCP must access storage ONLY via API HTTP interfaces, never directly via `src.common.storage.*`; `src.common.storage.*` modules are internal to the API server implementation.
  - Other `src.common.*` modules (web utils, interfaces, storage) are for the API server and scripts, not for MCP.
  - **Note:** Authentication is skipped for local-only deployment (MCP and API run on same machine)
- Update `mcp/handlers_*.py` so handlers no longer receive DB sessions or `SearchService` instances:
  - **Use `CHLAPIClient` from `src.common.api_client.client`** for all HTTP communication
  - Replace repository calls with HTTP requests for `list_categories`, `read_entries`, `write_entry`, etc.
  - Remove direct FAISS/SQLite logic; defer to API responses for degraded vs vector metadata
  - Delete helper code that inspects `search_service` state (e.g., `_runtime_search_mode`)
- Port any useful error-mapping or request-shaping logic from `src/mcp/api_client.py` into the shared `CHLAPIClient`, but **do not** add circuit breakers or automatic retries. `CHLAPIClient` raises standard HTTP exceptions (404, 500, etc.) to the caller; MCP handlers should catch these exceptions and surface simple, local-only failures to the host editor.
- **Error handling**: If the API server is unavailable, `CHLAPIClient` raises standard HTTP exceptions which MCP should surface as clear "API unreachable" errors to the client, with no automatic reconnection, health checks, or circuit breaker for connection failures (local deployment assumption).
- Delete `src/server.py` and `src/mcp/api_client.py`

**17. TEST MCP** ✅ **CHECKPOINT 3**
- Start API server (CPU or GPU mode)
- Start: `python -m src.mcp.server`
- Verify all MCP tools, HTTP-only communication
- **MCP must have zero direct DB/FAISS access**

### Phase 4: Cleanup

**18. Remove Old Code**
- Delete: `src/modes/`, `src/embedding/`, `src/search/`, `src/services/`
- Delete: `src/api_client.py`, `src/config.py`, `src/api_server.py`, `src/server.py`, `src/web/`
- Delete: `src/mcp/api_client.py`
- Verify no references: `grep -r "from src.modes" src/` (should return nothing)

**19. Update Scripts** (`scripts/` directory - 13+ files)

**Global Import Updates (All Scripts):**
```python
# Before → After
from src.api_client import CHLAPIClient → from src.common.api_client.client import CHLAPIClient
from src.config import Config → from src.common.config.config import Config
from src.storage import → from src.common.storage import
```

**Per-Script Migration Strategy:**

| Script | Strategy | Implementation Details | Rationale |
|--------|----------|------------------------|-----------|
| **import.py** | HTTP + Mode Detection | • Use `sheets_client.py` (common) to read spreadsheet<br>• POST `/api/v1/operations/import-sheets` via CHLAPIClient<br>• GET `/api/v1/settings/` to detect mode<br>• If GPU: POST `/api/v1/operations/sync-embeddings` | CPU: DB write only<br>GPU: DB write + trigger embedding job |
| **export.py** | Pure HTTP | • GET `/api/v1/entries/export` via CHLAPIClient<br>• Write to spreadsheet using `sheets_client.py` | Mode-agnostic read operation |
| **seed_default_content.py** | HTTP + Script Delegation | • Update imports: `src.config` → `src.common.config.config`<br>• When calling setup scripts, use subprocess (no dynamic imports) | Orchestrates other scripts |
| **rebuild_index.py** | HTTP | • POST `/api/v1/operations/rebuild-index` via CHLAPIClient<br>• Poll job status via GET `/api/v1/operations/jobs/{job_id}` | Mode-aware rebuild handled by API |
| **sync_embeddings.py** | HTTP | • POST `/api/v1/operations/sync-embeddings` via CHLAPIClient<br>• Poll job status via GET `/api/v1/operations/jobs/{job_id}` | GPU-specific operation |
| **sync_guidelines.py** | HTTP | • POST `/api/v1/operations/sync-guidelines` via CHLAPIClient<br>• Poll job status via GET `/api/v1/operations/jobs/{job_id}` if needed | Mode-agnostic |
| **setup-gpu.py** | Keep API Imports (Exception) | • Import from `src.api.gpu.embedding_service`<br>• Import from `src.common.storage`<br>• One-time setup, tightly coupled to GPU internals<br>• **Critical**: Move GPU-specific features from API server code to this script or other scripts in `scripts/` folder | Runs FIRST before API server starts (with API server **stopped**) |
| **setup-cpu.py** | HTTP + Common | • Use `src.common.storage` for DB setup<br>• Call API endpoints for validation | CPU-specific setup (prefer to run with API server stopped when mutating schema) |
| **gpu_smoke_test.py** | Keep API Imports (Exception) | • Import from `src.api.gpu.*` for direct testing<br>• Bypass HTTP to test internal components | Testing GPU internals (run independently of API server) |
| **search_health.py** | HTTP | • GET `/api/v1/search/health` via CHLAPIClient<br>• GET `/api/v1/settings/` for mode detection | Diagnostic tool |

**Note:** Legacy `scripts/tweak/read.py` and `scripts/tweak/write.py` are removed. Use API endpoints for debugging and maintenance instead.

**Exception Policy:**
- **Setup scripts** (`setup-gpu.py`, `gpu_smoke_test.py`): Can import from `src.api.*` because they configure/test internal components during initial environment setup
- **All other scripts**: Must use HTTP via CHLAPIClient or common utilities only

**Test each script** after migration to ensure functionality

**20. Update Tests**
- Fix all import paths in test files:
  - `from src.modes import` → `from src.api.cpu.runtime import` / `from src.api.gpu.runtime import`
  - `from src.services import` → `from src.api.services import`
  - `from src.storage import` → `from src.common.storage import`
  - `from src.api_client import` → `from src.common.api_client.client import`
  - etc.
- Add `tests/architecture/test_boundaries.py`: Static import checks to enforce architectural boundaries (implementation details left to developer)
  - MCP may only import from `src.common.config.*`, `src.common.api_client.*`, and `src.common.dto.*`
  - MCP must never import from `src.api.*`, `src.common.storage.*`, `src.common.interfaces.*`, or `src.common.web_utils.*`
  - CPU must never import from `src.api.gpu.*`
  - Common must never import from `src.api.*` or `src.mcp.*`
  - Scripts (except setup/test scripts) must never import from `src.api.*`
- Add integration and smoke tests as needed during implementation
- Update `tests/api/conftest.py`, `tests/integration/test_concurrent_faiss*.py` to reference `src.api.server`
- Update any docs that import `src.api_server` (README, `doc/manual.md`) to reference `src.api.server`

**21. Update Documentation**
- `doc/architecture/phase_0_boundaries.md`: Document structure and import rules
- `doc/architecture/cpu_gpu_strategy.md`: Document strategy pattern
- `doc/migration/phase_0_migration.md`: Migration guide
- Update `README.md`: New entry points and structure
- Update any developer guides with new import paths

## Import Rules

### Allowed
```python
# Scripts/External → common (shared API client + utilities)
from src.common.api_client.client import CHLAPIClient
from src.common.config.config import Config
from src.common.storage.database import Database
from src.common.storage.sheets_client import GoogleSheetsClient

# Setup/test scripts → api (EXCEPTION - for initial setup/testing only)
from src.api.gpu.embedding_service import EmbeddingService  # setup-gpu.py, gpu_smoke_test.py
from src.api.services.search_service import SearchService   # Test fixtures only

# MCP → common (Config, API client, shared DTOs ONLY)
from src.common.config.config import Config
from src.common.api_client.client import CHLAPIClient
# NOTE: MCP must not import from src.common.storage.* or src.common.web_utils.*;
# all storage and web concerns are reached only via HTTP JSON payloads.

# API → common
from src.common.storage.database import Database
from src.common.interfaces.embedding import EmbeddingProvider
from src.common.interfaces.search import SearchProvider
from src.common.interfaces.search_models import SearchResult
from src.common.api_client.client import CHLAPIClient

# API CPU/GPU → own implementations + common
from src.api.cpu.runtime import build_cpu_runtime
from src.api.gpu.runtime import build_gpu_runtime
from src.common.interfaces.runtime import ModeRuntime

# API services → CPU/GPU implementations (via runtime builder)
from src.api.runtime_builder import build_mode_runtime
```

### Prohibited
```python
# ❌ MCP → API (FORBIDDEN - violates separation)
from src.api.services.search_service import SearchService
from src.api.gpu.embedding_service import EmbeddingService

# ❌ MCP → common.storage/interfaces/web_utils (FORBIDDEN - use HTTP API only)
from src.common.storage.database import Database
from src.common.storage.repository import Repository
from src.common.interfaces.search import SearchProvider
from src.common.web_utils.docs import render_markdown

# ❌ CPU → GPU (FORBIDDEN - violates isolation)
from src.api.gpu.embedding_client import GPUEmbeddingClient

# ❌ Common → API/MCP (FORBIDDEN - creates circular dependency)
from src.api.services import anything
from src.mcp.server import anything
from src.api.runtime_builder import build_mode_runtime

# ❌ Operational scripts → API internals (FORBIDDEN - use HTTP via CHLAPIClient)
# Exceptions: setup-gpu.py, gpu_smoke_test.py (setup/testing only)
from src.api.services.search_service import SearchService  # in import.py, sync_embeddings.py
from src.api.gpu.embedding_service import EmbeddingService # in rebuild_index.py
```

## Entry Points

```bash
# API Server (CPU mode)
CHL_SEARCH_MODE=cpu python -m src.api.server

# API Server (GPU mode)
CHL_SEARCH_MODE=gpu python -m src.api.server

# MCP Server (requires API running)
python -m src.mcp.server

# Scripts (mode-aware via HTTP)
python scripts/import.py    # Detects mode, triggers embeddings if GPU
python scripts/export.py    # Mode-agnostic
```

## Success Criteria

- [ ] All directories match target structure
- [ ] Shared API client migrated to `common/api_client/`
- [ ] Search models migrated to `common/interfaces/search_models.py`
- [ ] Runtime builder created in `api/runtime_builder.py`
- [ ] Templates organized in `api/templates/{common,cpu,gpu}/`
- [ ] Web utilities in `common/web_utils/`
- [ ] CPU mode fully functional (Checkpoint 1)
- [ ] GPU mode fully functional (Checkpoint 2)
- [ ] MCP server fully functional (Checkpoint 3)
- [ ] Old directories deleted (modes/, embedding/, search/, services/, storage/, web/)
- [ ] Old files deleted (api_client.py, config.py, api_server.py, server.py, mcp/api_client.py)
- [ ] All scripts updated per migration table (13+ files)
- [ ] All tests pass
- [ ] Boundary tests prevent future coupling
- [ ] Documentation updated
- [ ] No import rule violations

## Timeline Estimate

- **Phase 1 (Foundation & CPU)**: 17 hours
  - +3 hours for web_utils refactor, mcp/api_client removal, runtime.py protocol clarity
- **Phase 2 (GPU)**: 11 hours
  - +1 hour for embedding_service.py migration
- **Phase 3 (MCP)**: 6 hours
  - +1 hour for CHLAPIClient integration in handlers
- **Phase 4 (Cleanup)**: 14 hours
  - +6 hours for scripts migration table implementation (mode-aware import/export)
- **Total**: ~48 hours (6 days)

## Key Improvements from v1

### Critical Fixes
1. **Simplified web structure**: `common/web_utils/` + `api/templates/` (no more confusing "common/web" vs "web/common")
2. **Single API client**: Deleted `mcp/api_client.py`, use `CHLAPIClient` everywhere
3. **Scripts migration table**: Per-script strategy with mode-aware import/export documented
4. **Clearer protocols**: `common/interfaces/runtime.py` uses typing.Protocol ONLY, no src.api.* imports
5. **Removed optional modules**: No `common/models/domain.py` (use api/mcp DTOs directly)

### Alignment with High-Level Goals
- Supports "API venv per platform + MCP via uv sync" workflow
- Scripts can detect mode and orchestrate appropriately via HTTP
- Clear separation enables Phase A (platform-specific requirements)
- Foundation for Phase B (diagnostics script)

---

## Next Steps (Phase A)

After Phase 0 completion:
- Create `requirements_cpu.txt` for API server CPU mode
- Create `requirements_gpu_*.txt` for API server GPU modes (Metal, CUDA, ROCm, oneAPI)
- Document "API venv per platform + MCP via uv sync" workflow
- Platform-specific installation guides
