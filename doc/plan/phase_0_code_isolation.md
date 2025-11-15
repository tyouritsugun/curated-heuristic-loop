# Phase 0: Codebase Isolation Implementation Plan

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
│   ├── runtime_builder.py                 # CREATE: Factory for building CPU/GPU runtimes (extracted from modes/base.py)
│   ├── dependencies.py                    # UPDATE: Fix imports, use runtime_builder
│   ├── metrics.py                         # UPDATE: Fix imports
│   ├── models.py                          # NO CHANGE: API-specific DTOs
│   └── server.py                          # CREATE: Move from api_server.py, implement runtime selection
├── mcp/                                    # MCP server code ONLY
│   ├── api_client.py                      # UPDATE: Fix imports (src.config → src.common.config.config)
│   ├── errors.py                          # NO CHANGE
│   ├── utils.py                           # UPDATE: Fix imports if needed
│   ├── models.py                          # NO CHANGE: MCP-specific DTOs
│   ├── handlers_entries.py                # UPDATE: Fix imports
│   ├── handlers_guidelines.py             # UPDATE: Fix imports
│   └── server.py                          # CREATE: Move from server.py, update imports
├── common/                                 # Shared infrastructure ONLY
│   ├── api_client/                        # HTTP client for API server (used by scripts, MCP)
│   │   ├── client.py                      # MIGRATE: Move from api_client.py (CHLAPIClient)
│   │   └── errors.py                      # CREATE: Extract CHLAPIError, APIConnectionError, APIOperationError
│   ├── config/                            # Configuration management
│   │   ├── config.py                      # MIGRATE: Move from config.py
│   │   └── env.py                         # CREATE: Environment variable parsing (if needed)
│   ├── storage/                           # Database and repositories
│   │   ├── database.py                    # MIGRATE: Move from storage/
│   │   ├── repository.py                  # MIGRATE: Move from storage/
│   │   ├── schema.py                      # MIGRATE: Move from storage/
│   │   └── sheets_client.py               # MIGRATE: Move from storage/
│   ├── models/                            # Shared domain models
│   │   └── domain.py                      # CREATE: Extract domain models if needed (optional)
│   ├── interfaces/                        # Abstract interfaces/protocols
│   │   ├── embedding.py                   # CREATE: EmbeddingProvider protocol
│   │   ├── search.py                      # CREATE: SearchProvider protocol + SearchProviderError
│   │   ├── search_models.py               # CREATE: SearchResult, DuplicateCandidate, SearchReason (from search/models.py)
│   │   └── runtime.py                     # CREATE: ModeRuntime dataclass ONLY (from modes/base.py)
│   └── web/                               # Shared web resources
│       ├── static/                        # MIGRATE: Move from web/static/
│       │   ├── css/
│       │   └── favicon.ico
│       └── docs.py                        # MIGRATE: Move from web/docs.py (markdown rendering)
└── web/                                    # Mode-specific web UI
    ├── common/                            # Shared templates across modes
    │   └── templates/                     # CREATE: Shared templates (base.html, doc_viewer.html, partials/*)
    │       ├── base.html
    │       ├── doc_viewer.html
    │       └── partials/
    │           ├── sidebar.html
    │           ├── flash.html
    │           ├── audit_log.html
    │           ├── config_status_card.html
    │           ├── diagnostics_panel.html
    │           └── sheets_card.html
    ├── cpu/                               # CPU-mode templates
    │   └── templates/                     # CREATE: CPU-specific templates
    │       ├── settings_cpu.html
    │       ├── operations_cpu.html
    │       └── partials/
    │           ├── ops_onboarding_cpu.html
    │           └── settings_onboarding_cpu.html
    └── gpu/                               # GPU-mode templates
        └── templates/                     # CREATE: GPU-specific templates
            ├── settings.html
            ├── operations_gpu.html
            └── partials/
                ├── ops_onboarding_gpu.html
                ├── settings_gpu_runtime.html
                ├── settings_onboarding_gpu.html
                ├── models_card.html
                ├── ops_models_card.html
                ├── ops_queue_card.html
                ├── ops_operations_card.html
                ├── ops_jobs_card.html
                └── model_change_modal.html

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
└── web/templates/                          # DELETE: Split into web/common/, web/cpu/, and web/gpu/
```

## Migration Actions by Component

### Phase 1: Foundation & CPU

**1. Create Directory Structure**
- Create all new directories listed above (no `__init__.py` files)

**2. Define Common Interfaces** (`src/common/interfaces/`)
- `search_models.py`: Migrate `SearchResult`, `DuplicateCandidate`, `SearchReason` from `search/models.py`
- `search.py`: Protocol with `is_available`, `search()`, `find_duplicates()`, `rebuild_index()` + `SearchProviderError` exception
- `embedding.py`: Protocol with `encode()`, `encode_single()`, `embedding_dimension`, `get_model_version()`
- `runtime.py`: **ONLY** the `ModeRuntime` dataclass (from `modes/base.py`, NOT `build_mode_runtime()`)

**3. Migrate Shared API Client** (`src/common/api_client/`)
- Move `src/api_client.py` → `src/common/api_client/client.py`
- Extract exceptions to `src/common/api_client/errors.py`:
  - `CHLAPIError`, `APIConnectionError`, `APIOperationError`
- Update imports: `from src.api_client import CHLAPIClient` → `from src.common.api_client.client import CHLAPIClient`

**4. Migrate Configuration**
- Move `src/config.py` → `src/common/config/config.py`
- **Audit**: Verify no imports from `src.api.*` or `src.mcp.*` (config must be pure)
- Update all imports: `from src.config import` → `from src.common.config.config import`

**5. Migrate Storage**
- Move `src/storage/*` → `src/common/storage/*`
- Update all imports: `from src.storage` → `from src.common.storage`

**6. Migrate Web Rendering**
- Move `src/web/docs.py` → `src/common/web/docs.py`
- Move `src/web/static/*` → `src/common/web/static/*`
- Split `src/web/templates/*`:
  - Shared templates → `src/web/common/templates/` (base.html, doc_viewer.html, partials/sidebar.html, etc.)
  - CPU templates → `src/web/cpu/templates/` (settings_cpu.html, operations_cpu.html, partials/ops_onboarding_cpu.html)
  - GPU templates → `src/web/gpu/templates/` (settings.html, operations_gpu.html, GPU-specific partials)
- Update Jinja2 template loader to search: `[mode]/templates/`, then `common/templates/`

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

**9. Create Runtime Builder** (`src/api/runtime_builder.py`)
- Extract `build_mode_runtime()` function from `modes/base.py`
- Import from `api/cpu/runtime.py` and `api/gpu/runtime.py` (set up for Phase 2)
- This avoids `common → api` boundary violation

**10. Consolidate API Server**
- Move `src/api_server.py` → `src/api/server.py`
- Update to import from `api/runtime_builder.py`
- Update `src/api/dependencies.py` for new paths
- Update all `src/api/routers/*` imports
- Delete `src/api_server.py`

**11. TEST CPU MODE** ✅ **CHECKPOINT 1**
- Set `CHL_SEARCH_MODE=sqlite_only`
- Start: `python -m src.api.server`
- Verify all endpoints, web UI, SQLite search work
- **DO NOT PROCEED until CPU mode fully validated**

### Phase 2: GPU

**12. Extract GPU Implementation** (`src/api/gpu/`)
- Migrate `embedding/client.py` → `api/gpu/embedding_client.py`
- Migrate `embedding/service.py` → `api/gpu/embedding_service.py` (GPU-only, 663 lines)
- Migrate `embedding/reranker.py` → `api/gpu/reranker_client.py`
- Migrate `search/faiss_index.py` + `search/thread_safe_faiss.py` → `api/gpu/faiss_manager.py`
- Migrate `search/vector_provider.py` → `api/gpu/search_provider.py`
- Migrate `modes/vector/runtime.py` → `api/gpu/runtime.py`
- Update imports to use `common/interfaces/*`

**13. Update Services for GPU**
- Update `background_worker.py`: Import `api/gpu/embedding_service.py` conditionally (GPU mode only)
- Update `search_service.py`: Already protocol-based, should work with GPU provider
- No changes to other services (GPU isolation complete)

**14. Integrate GPU Runtime**
- Update `src/api/runtime_builder.py` to import from `api/gpu/runtime.py`
- Update routers if needed for GPU templates
- Verify template loader works for GPU mode

**15. TEST GPU MODE** ✅ **CHECKPOINT 2**
- Set `CHL_SEARCH_MODE=auto`
- Start: `python -m src.api.server`
- Verify FAISS, embeddings, reranking, background worker, web UI
- **DO NOT PROCEED until GPU mode fully validated**

### Phase 3: MCP

**16. Isolate MCP Server**
- Move `src/server.py` → `src/mcp/server.py`
- Audit: MCP should ONLY import from `common/` (verify no `src.api.*` imports)
- Update `mcp/api_client.py` imports: `src.config` → `src.common.config.config`
- Update `mcp/handlers_*.py` imports
- Delete `src/server.py`

**17. TEST MCP** ✅ **CHECKPOINT 3**
- Start API server (CPU or GPU mode)
- Start: `python -m src.mcp.server`
- Verify all MCP tools, HTTP-only communication
- **MCP must have zero direct DB/FAISS access**

### Phase 4: Cleanup

**18. Remove Old Code**
- Delete: `src/modes/`, `src/embedding/`, `src/search/`, `src/services/`
- Delete: `src/api_client.py`, `src/config.py`, `src/api_server.py`, `src/server.py`
- Verify no references: `grep -r "from src.modes" src/`

**19. Update Scripts** (`scripts/` directory - 13+ files)
- Fix imports in all Python scripts:
  - `from src.api_client import` → `from src.common.api_client.client import`
  - `from src.config import` → `from src.common.config.config import`
  - `from src.storage import` → `from src.common.storage import`
  - `from src.search.service import` → `from src.api.services.search_service import`
  - `from src.embedding.service import` → `from src.api.gpu.embedding_service import`
- **Scripts to update**:
  - `import.py`, `export.py`
  - `rebuild_index.py`, `sync_embeddings.py`, `sync_guidelines.py`
  - `setup-gpu.py`, `setup-cpu.py`
  - `gpu_smoke_test.py`, `search_health.py`
  - `scripts/tweak/read.py`, `scripts/tweak/write.py`
- **Test each script** to ensure functionality

**20. Update Tests**
- Fix all import paths in test files:
  - `from src.modes import` → `from src.api.cpu.runtime import` / `from src.api.gpu.runtime import`
  - `from src.services import` → `from src.api.services import`
  - `from src.storage import` → `from src.common.storage import`
  - etc.
- Add `tests/architecture/test_boundaries.py`: Static import checks
  - Ensure MCP never imports from `src.api.*`
  - Ensure CPU never imports from `src.api.gpu.*`
  - Ensure common never imports from `src.api.*` or `src.mcp.*`
  - Use AST parsing or grep to validate import rules
- Add `tests/smoke/test_startup.py`: Mode-specific startup tests
  - Test CPU mode starts without GPU dependencies
  - Test GPU mode starts with FAISS/embeddings available
  - Test MCP starts and communicates with API
- Add `tests/integration/test_search_providers.py`: Provider isolation tests
  - Test SQLite provider works independently
  - Test Vector provider graceful degradation
  - Test search service fallback logic

**21. Update Documentation**
- `doc/architecture/phase_0_boundaries.md`: Document structure and import rules
- `doc/architecture/cpu_gpu_strategy.md`: Document strategy pattern
- `doc/migration/phase_0_migration.md`: Migration guide
- Update `README.md`: New entry points and structure
- Update any developer guides with new import paths

## Import Rules

### Allowed
```python
# Scripts/External → common (shared API client)
from src.common.api_client.client import CHLAPIClient
from src.common.config.config import Config
from src.common.storage.database import Database

# MCP → common ONLY
from src.common.config.config import Config
from src.common.api_client.client import CHLAPIClient
from src.common.storage.schema import Experience

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

# ❌ CPU → GPU (FORBIDDEN - violates isolation)
from src.api.gpu.embedding_client import GPUEmbeddingClient

# ❌ Common → API/MCP (FORBIDDEN - creates circular dependency)
from src.api.services import anything
from src.mcp.api_client import APIClient
from src.api.runtime_builder import build_mode_runtime

# ❌ Scripts → API internals (FORBIDDEN - use common/api_client instead)
from src.api.services.search_service import SearchService
```

## Entry Points

```bash
# API Server (CPU mode)
CHL_SEARCH_MODE=sqlite_only python -m src.api.server

# API Server (GPU mode)
CHL_SEARCH_MODE=auto python -m src.api.server

# MCP Server (requires API running)
python -m src.mcp.server
```

## Success Criteria

- [ ] All directories match target structure
- [ ] Shared API client migrated to `common/api_client/`
- [ ] Search models migrated to `common/interfaces/search_models.py`
- [ ] Runtime builder created in `api/runtime_builder.py`
- [ ] Shared templates in `web/common/templates/`
- [ ] CPU mode fully functional (Checkpoint 1)
- [ ] GPU mode fully functional (Checkpoint 2)
- [ ] MCP server fully functional (Checkpoint 3)
- [ ] Old directories deleted
- [ ] All scripts updated and tested (13+ files)
- [ ] All tests pass
- [ ] Boundary tests prevent future coupling
- [ ] Documentation updated
- [ ] No import rule violations

## Timeline Estimate

- **Phase 1 (Foundation & CPU)**: 16 hours
  - +2 hours for api_client migration, search_models, runtime_builder, shared templates
- **Phase 2 (GPU)**: 11 hours
  - +1 hour for embedding_service.py migration
- **Phase 3 (MCP)**: 5 hours
- **Phase 4 (Cleanup)**: 12 hours
  - +4 hours for scripts update (13+ files), expanded test coverage
- **Total**: ~44 hours (5-6 days)

## Key Improvements from Review

This plan has been updated based on comprehensive codebase analysis (see `phase_0_review.md`):

### Critical Additions
1. **Shared API Client** (`common/api_client/`) - Used by 13+ scripts and external tools
2. **Scripts Update** (Phase 4, Step 19) - Fix imports in all operational scripts
3. **Runtime Builder** (`api/runtime_builder.py`) - Avoids `common → api` boundary violation

### Important Clarifications
4. **Embedding Service** → GPU-only (`api/gpu/embedding_service.py`, not in `api/services/`)
5. **Search Models** → Protocol contracts (`common/interfaces/search_models.py`)
6. **Search Service** → Migrated from `search/service.py` to `api/services/search_service.py`
7. **Shared Templates** → New `web/common/templates/` for base.html, partials, etc.

### Enhanced Testing
8. **Boundary Tests** - AST-based import validation to prevent coupling
9. **Smoke Tests** - Mode-specific startup validation (CPU/GPU/MCP)
10. **Integration Tests** - Provider isolation and fallback logic

### Files Accounted For
- **51 Python files** in src/
- **13+ scripts** requiring import updates
- **23 HTML templates** properly segregated (common/CPU/GPU)

**Review Document**: See `doc/plan/phase_0_review.md` for detailed analysis

---

## Next Steps (Phase A)

After Phase 0 completion:
- Create `requirements_cpu.txt` for API server CPU mode
- Create `requirements_gpu_*.txt` for API server GPU modes (Metal, CUDA, ROCm, oneAPI)
- Document "API venv per platform + MCP via uv sync" workflow
- Platform-specific installation guides
