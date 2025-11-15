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
│   │   ├── search_provider.py             # BUILD: Migrate from search/sqlite_provider.py
│   │   └── embedding_service.py           # BUILD: No-op stub (optional)
│   ├── gpu/                               # GPU-accelerated implementations
│   │   ├── runtime.py                     # BUILD: Migrate from modes/vector/runtime.py
│   │   ├── embedding_client.py            # BUILD: Migrate from embedding/client.py
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
│   ├── services/                          # Business services with strategy pattern
│   │   ├── embedding_service.py           # MIGRATE + REFACTOR: Move from services/, inject EmbeddingProvider protocol
│   │   ├── search_service.py              # MIGRATE + REFACTOR: Move from services/, use SearchProvider protocol
│   │   ├── settings_service.py            # MIGRATE: Move from services/, update imports
│   │   ├── operations_service.py          # MIGRATE: Move from services/, update imports
│   │   ├── worker_control.py              # MIGRATE: Move from services/, update imports
│   │   ├── background_worker.py           # MIGRATE + REFACTOR: Move from services/, accept EmbeddingProvider protocol
│   │   ├── telemetry_service.py           # MIGRATE: Move from services/, update imports
│   │   ├── telemetry_names.py             # MIGRATE: Move from services/
│   │   └── gpu_installer.py               # MIGRATE: Move from services/
│   ├── dependencies.py                    # UPDATE: Fix imports, use new runtime builder
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
│   │   ├── search.py                      # CREATE: SearchProvider protocol
│   │   └── runtime.py                     # CREATE: ModeRuntime dataclass (from modes/base.py)
│   └── web/                               # Shared web resources
│       ├── static/                        # MIGRATE: Move from web/static/
│       │   ├── css/
│       │   └── favicon.ico
│       └── docs.py                        # MIGRATE: Move from web/docs.py (markdown rendering)
└── web/                                    # Mode-specific web UI
    ├── cpu/                               # CPU-mode templates
    │   └── templates/                     # CREATE: Split from web/templates/ (settings_cpu.html, operations_cpu.html, etc.)
    └── gpu/                               # GPU-mode templates
        └── templates/                     # CREATE: Split from web/templates/ (settings.html, operations_gpu.html, etc.)

# DELETED (old structure):
├── modes/                                  # DELETE: Migrated to api/cpu/ and api/gpu/
├── embedding/                              # DELETE: Migrated to api/gpu/
├── search/                                 # DELETE: Migrated to api/cpu/ and api/gpu/
├── services/                               # DELETE: Migrated to api/services/
├── storage/                                # DELETE: Migrated to common/storage/
├── config.py                               # DELETE: Migrated to common/config/config.py
├── server.py                               # DELETE: Moved to mcp/server.py
├── api_server.py                           # DELETE: Moved to api/server.py
└── web/templates/                          # DELETE: Split into web/cpu/templates/ and web/gpu/templates/
```

## Migration Actions by Component

### Phase 1: Foundation & CPU

**1. Create Directory Structure**
- Create all new directories listed above (no `__init__.py` files)

**2. Define Common Interfaces** (`src/common/interfaces/`)
- `embedding.py`: Protocol with `encode()`, `encode_single()`, `embedding_dimension`, `get_model_version()`
- `search.py`: Protocol with `is_available`, `search()`
- `runtime.py`: Dataclass for `ModeRuntime` (from `modes/base.py`)

**3. Migrate Configuration**
- Move `src/config.py` → `src/common/config/config.py`
- Update all imports: `from src.config import` → `from src.common.config.config import`

**4. Migrate Storage**
- Move `src/storage/*` → `src/common/storage/*`
- Update all imports: `from src.storage` → `from src.common.storage`

**5. Migrate Web Rendering**
- Move `src/web/docs.py` → `src/common/web/docs.py`
- Move `src/web/static/*` → `src/common/web/static/*`
- Split `src/web/templates/*`:
  - CPU templates → `src/web/cpu/templates/`
  - GPU templates → `src/web/gpu/templates/`

**6. Extract CPU Implementation** (`src/api/cpu/`)
- Migrate `modes/sqlite_only/runtime.py` → `api/cpu/runtime.py`
- Migrate `search/sqlite_provider.py` → `api/cpu/search_provider.py`
- Optional: Create `api/cpu/embedding_service.py` (no-op stub)

**7. Migrate API Services** (`src/api/services/`)
- Move all from `src/services/*` → `src/api/services/*`
- Update imports to use `common/*` and protocols
- `search_service.py`: Use `SearchProvider` protocol
- `embedding_service.py`: Inject `EmbeddingProvider` protocol
- `background_worker.py`: Accept `EmbeddingProvider` protocol

**8. Consolidate API Server**
- Move `src/api_server.py` → `src/api/server.py`
- Update runtime builder to select CPU/GPU based on config
- Update `src/api/dependencies.py` for new paths
- Update all `src/api/routers/*` imports
- Delete `src/api_server.py`

**9. TEST CPU MODE** ✅ **CHECKPOINT 1**
- Set `CHL_SEARCH_MODE=sqlite_only`
- Start: `python -m src.api.server`
- Verify all endpoints, web UI, SQLite search work
- **DO NOT PROCEED until CPU mode fully validated**

### Phase 2: GPU

**10. Extract GPU Implementation** (`src/api/gpu/`)
- Migrate `embedding/client.py` → `api/gpu/embedding_client.py`
- Migrate `embedding/reranker.py` → `api/gpu/reranker_client.py`
- Migrate `search/faiss_index.py` + `search/thread_safe_faiss.py` → `api/gpu/faiss_manager.py`
- Migrate `search/vector_provider.py` → `api/gpu/search_provider.py`
- Migrate `modes/vector/runtime.py` → `api/gpu/runtime.py`

**11. Update Services for GPU**
- Update `embedding_service.py`: Add GPU provider factory
- Update `background_worker.py`: Support both CPU/GPU providers
- Update `search_service.py`: Support GPU vector provider

**12. Integrate GPU Runtime**
- Update `src/api/server.py` runtime builder to support GPU mode
- Update routers if needed for GPU templates

**13. TEST GPU MODE** ✅ **CHECKPOINT 2**
- Set `CHL_SEARCH_MODE=auto`
- Start: `python -m src.api.server`
- Verify FAISS, embeddings, reranking, background worker, web UI
- **DO NOT PROCEED until GPU mode fully validated**

### Phase 3: MCP

**14. Isolate MCP Server**
- Move `src/server.py` → `src/mcp/server.py`
- Audit: MCP should ONLY import from `common/`
- Update `mcp/api_client.py` imports
- Update `mcp/handlers_*.py` imports
- Delete `src/server.py`

**15. TEST MCP** ✅ **CHECKPOINT 3**
- Start API server (CPU or GPU mode)
- Start: `python -m src.mcp.server`
- Verify all MCP tools, HTTP-only communication
- **MCP must have zero direct DB/FAISS access**

### Phase 4: Cleanup

**16. Remove Old Code**
- Delete: `src/modes/`, `src/embedding/`, `src/search/`, `src/services/`
- Verify no references: `grep -r "from src.modes" src/`

**17. Update Documentation**
- `doc/architecture/phase_0_boundaries.md`: Document structure and import rules
- `doc/architecture/cpu_gpu_strategy.md`: Document strategy pattern
- `doc/migration/phase_0_migration.md`: Migration guide
- Update `README.md`: New entry points and structure

**18. Update Tests**
- Fix all import paths in test files
- Add `tests/test_boundaries.py`: Static checks for prohibited imports
- Add smoke tests for startup

## Import Rules

### Allowed
```python
# MCP → common
from src.common.config.config import Config

# API → common
from src.common.storage.database import Database
from src.common.interfaces.embedding import EmbeddingProvider

# API CPU/GPU → own implementations
from src.api.cpu.runtime import build_cpu_runtime
from src.api.gpu.runtime import build_gpu_runtime
```

### Prohibited
```python
# ❌ MCP → API (FORBIDDEN)
from src.api.services.search_service import SearchService

# ❌ CPU → GPU (FORBIDDEN)
from src.api.gpu.embedding_client import GPUEmbeddingClient

# ❌ Common → API/MCP (FORBIDDEN)
from src.api.services import anything
from src.mcp.api_client import APIClient
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
- [ ] CPU mode fully functional (Checkpoint 1)
- [ ] GPU mode fully functional (Checkpoint 2)
- [ ] MCP server fully functional (Checkpoint 3)
- [ ] Old directories deleted
- [ ] All tests pass
- [ ] Documentation updated
- [ ] Boundary tests prevent future coupling

## Timeline Estimate

- **Phase 1 (CPU)**: 14 hours
- **Phase 2 (GPU)**: 10 hours
- **Phase 3 (MCP)**: 5 hours
- **Phase 4 (Cleanup)**: 8 hours
- **Total**: ~37 hours (4-5 days)

## Next Steps (Phase A)

After Phase 0 completion:
- Create `requirements_cpu.txt` for API server CPU mode
- Create `requirements_gpu_*.txt` for API server GPU modes (Metal, CUDA, ROCm, oneAPI)
- Document "API venv per platform + MCP via uv sync" workflow
- Platform-specific installation guides
