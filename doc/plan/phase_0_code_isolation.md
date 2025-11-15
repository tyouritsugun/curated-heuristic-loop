# Phase 0: Codebase Isolation Implementation Plan

## Overview
This phase restructures `src/` to cleanly separate the API server, MCP server, and shared components, while isolating CPU-specific and GPU-specific implementations within the API server behind well-defined interfaces.

**Migration Strategy**: Full cutover with no backward compatibility. All imports and entry points will be updated explicitly.

**Testing Strategy**:
1. Complete CPU implementation → Test API server in CPU mode
2. Complete GPU implementation → Test API server in GPU mode
3. Complete MCP isolation → Test MCP server with API

## Goals
1. **API ↔ MCP Separation**: Eliminate accidental shared state between API server and MCP server
2. **CPU ↔ GPU Isolation**: Within the API server, separate CPU-only and GPU-accelerated code paths using strategy patterns
3. **Clear Boundaries**: Document and enforce module boundaries to prevent coupling
4. **Foundation for Later Phases**: Enable Phase A-D work without extensive refactoring

## Current State Assessment

### Directory Structure (Before)
```
src/
├── api/                    # API routers only
│   └── routers/
├── mcp/                    # MCP handlers
├── embedding/              # Shared ML clients
├── search/                 # Shared search providers
├── services/               # Shared business logic
├── storage/                # Shared database/repository
├── modes/                  # Runtime mode builders
│   ├── base.py
│   ├── sqlite_only/
│   └── vector/
├── web/                    # Web UI
├── server.py               # MCP entry point
├── api_server.py           # API entry point
└── config.py               # Shared configuration
```

### Key Coupling Issues
1. **API ↔ MCP Coupling**:
   - Both import directly from `embedding/`, `search/`, `services/`
   - MCP server (`server.py`) references API client patterns
   - Shared access to storage without clear interfaces

2. **CPU ↔ GPU Coupling**:
   - `embedding/client.py` contains `n_gpu_layers` parameter
   - `modes/` separation exists but components are still shared
   - No clear strategy pattern for switching implementations

3. **Cross-cutting Concerns**:
   - Configuration is global and affects both API and MCP
   - Database access is direct rather than through interfaces
   - Services mix business logic with infrastructure concerns

## Target State

### Directory Structure (After Phase 0)
```
src/
├── api/                          # API server code ONLY
│   ├── cpu/                      # CPU-only mode implementation
│   │   ├── runtime.py           # CPU runtime builder
│   │   ├── embedding_service.py # No-op or fallback embedding
│   │   └── search_provider.py   # SQLite-based search
│   ├── gpu/                      # GPU-accelerated mode implementation
│   │   ├── runtime.py           # GPU runtime builder
│   │   ├── embedding_client.py  # GGUF embedding with GPU offload
│   │   ├── reranker_client.py   # GGUF reranker with GPU offload
│   │   ├── search_provider.py   # FAISS/vector search
│   │   └── faiss_manager.py     # FAISS index management
│   ├── routers/                  # FastAPI routers (unchanged)
│   ├── services/                 # Business services with strategy pattern
│   │   ├── embedding_service.py # Uses strategy for CPU/GPU
│   │   ├── search_service.py    # Uses strategy for providers
│   │   ├── settings_service.py
│   │   ├── operations_service.py
│   │   ├── worker_control.py
│   │   ├── background_worker.py
│   │   └── telemetry_service.py
│   ├── dependencies.py           # FastAPI dependencies
│   ├── metrics.py
│   ├── models.py                 # API-specific DTOs
│   └── server.py                 # API server entry point (moved from api_server.py)
├── mcp/                          # MCP server code ONLY
│   ├── api_client.py            # HTTP client to API server
│   ├── errors.py
│   ├── utils.py
│   ├── models.py                # MCP-specific DTOs
│   ├── handlers_entries.py
│   ├── handlers_guidelines.py
│   └── server.py                # MCP server entry point (moved from server.py)
├── common/                       # Shared infrastructure ONLY
│   ├── config/                  # Configuration management
│   │   ├── config.py            # Config class
│   │   └── env.py               # Environment variable parsing
│   ├── storage/                 # Database and repositories
│   │   ├── database.py
│   │   ├── repository.py
│   │   └── schema.py
│   ├── models/                  # Shared domain models
│   │   └── domain.py            # Experience, Manual, etc.
│   ├── interfaces/              # Abstract interfaces/protocols
│   │   ├── embedding.py         # EmbeddingProvider protocol
│   │   ├── search.py            # SearchProvider protocol
│   │   └── runtime.py           # ModeRuntime protocol
│   └── web/                     # Shared web resources
│       ├── static/              # Shared static assets
│       └── docs.py              # Markdown rendering (shared by CPU/GPU)
└── web/                          # Mode-specific web UI
    ├── cpu/                     # CPU-mode HTML templates
    │   └── templates/
    └── gpu/                     # GPU-mode HTML templates
        └── templates/
```

**Key Changes from Current Structure**:
- No `__init__.py` files (not supported)
- Web UI split: mode-specific HTML in `web/cpu/` and `web/gpu/`, shared rendering in `common/web/`
- Entry points explicitly moved: `api_server.py` → `api/server.py`, `server.py` → `mcp/server.py`
- All old directories (`modes/`, `embedding/`, `search/`, `services/`) removed
- No symlinks or backward compatibility layer

### Architectural Principles

#### 1. **API Server Isolation**
- **Owns**: All FastAPI routers, services, and runtime management
- **Dependencies**: `common/` only
- **No Access To**: `mcp/` internals
- **Entry Point**: `src/api/server.py` (explicit path, no symlink)

#### 2. **MCP Server Isolation**
- **Owns**: MCP tool handlers, HTTP client wrapper
- **Dependencies**: `common/config`, `common/models` for DTOs
- **No Direct Access To**: SQLite, FAISS, embeddings (goes through HTTP API)
- **Entry Point**: `src/mcp/server.py` (explicit path, no symlink)

#### 3. **Common Layer**
- **Provides**: Configuration, database, repositories, domain models, interfaces, shared web rendering
- **No Runtime Logic**: No search, embedding, or business logic
- **Stable Contracts**: Changes here affect both API and MCP

#### 4. **CPU/GPU Strategy Pattern**
Within `src/api/`, CPU and GPU implementations are behind protocols:

```python
# src/common/interfaces/embedding.py
from typing import Protocol
import numpy as np

class EmbeddingProvider(Protocol):
    """Abstract interface for embedding generation."""

    def encode(self, texts: list[str]) -> np.ndarray: ...
    def encode_single(self, text: str) -> np.ndarray: ...
    @property
    def embedding_dimension(self) -> int: ...
    def get_model_version(self) -> str: ...
```

**CPU Implementation** (`src/api/cpu/embedding_service.py`):
- Either no-op or fallback to simple embeddings
- No GPU dependencies

**GPU Implementation** (`src/api/gpu/embedding_client.py`):
- Uses `llama-cpp-python` with `n_gpu_layers` support
- GGUF model loading and inference

**Runtime Selection** (`src/api/services/embedding_service.py`):
```python
from common.interfaces.embedding import EmbeddingProvider

def create_embedding_provider(config) -> EmbeddingProvider:
    if config.is_cpu_only():
        from api.cpu.embedding_service import CPUEmbeddingService
        return CPUEmbeddingService()
    else:
        from api.gpu.embedding_client import GPUEmbeddingClient
        return GPUEmbeddingClient(
            model_repo=config.embedding_repo,
            quantization=config.embedding_quant,
        )
```

## Implementation Steps

### PHASE 1: Foundation & CPU Implementation

#### Step 1: Create Directory Structure
**Estimated Effort**: 30 minutes

1. Create new directories:
   ```bash
   mkdir -p src/api/{cpu,gpu,services}
   mkdir -p src/common/{config,storage,models,interfaces,web}
   mkdir -p src/web/{cpu/templates,gpu/templates}
   ```

**Success Criteria**:
- All new directories exist
- Directory structure matches target state

**Files Created**:
- All new directories

---

#### Step 2: Define Common Interfaces
**Estimated Effort**: 2 hours

Create protocol definitions in `src/common/interfaces/`:

1. **`embedding.py`**: `EmbeddingProvider` protocol
   ```python
   class EmbeddingProvider(Protocol):
       def encode(self, texts: list[str]) -> np.ndarray: ...
       def encode_single(self, text: str) -> np.ndarray: ...
       @property
       def embedding_dimension(self) -> int: ...
       def get_model_version(self) -> str: ...
   ```

2. **`search.py`**: `SearchProvider` protocol
   ```python
   class SearchProvider(Protocol):
       @property
       def is_available(self) -> bool: ...
       def search(
           self,
           query: str,
           category_code: str,
           entity_type: str,
           limit: int,
           session: Session,
       ) -> SearchResult: ...
   ```

3. **`runtime.py`**: `ModeRuntime` protocol
   ```python
   @dataclass
   class ModeRuntime:
       search_service: Optional[SearchService]
       thread_safe_faiss: Optional[Any]
       background_worker: Optional[Any]
       worker_pool: Optional[Any]
       operations_adapter: Optional[OperationsAdapter]
       diagnostics_adapter: Optional[DiagnosticsAdapter]
   ```

**Success Criteria**:
- All protocols defined with proper type hints
- No implementation code in protocol files
- Protocols can be imported using absolute paths

**Files Created**:
- `src/common/interfaces/embedding.py`
- `src/common/interfaces/search.py`
- `src/common/interfaces/runtime.py`

---

#### Step 3: Migrate Configuration to Common
**Estimated Effort**: 1 hour

1. Move `src/config.py` → `src/common/config/config.py`
2. Update all imports: `from src.config import` → `from src.common.config.config import`

**Success Criteria**:
- Configuration is only in `common/config/`
- All imports updated and working
- No circular dependencies

**Files Created/Modified**:
- `src/common/config/config.py` (moved from `src/config.py`)
- Update imports in ~20 files

**Files Deleted**:
- `src/config.py`

---

#### Step 4: Migrate Storage to Common
**Estimated Effort**: 1 hour

1. Move `src/storage/` → `src/common/storage/`
2. Update imports: `from src.storage import` → `from src.common.storage.schema import` (etc.)

**Success Criteria**:
- Storage layer only in `common/storage/`
- All database access goes through common layer
- All imports updated

**Files Created/Modified**:
- Move `src/storage/` directory to `src/common/storage/`
- Update imports in ~30 files

**Directories Deleted**:
- `src/storage/`

---

#### Step 5: Migrate Web Rendering to Common
**Estimated Effort**: 1 hour

1. Move `src/web/docs.py` → `src/common/web/docs.py` (markdown rendering)
2. Move shared static assets to `src/common/web/static/`
3. Split templates:
   - CPU-specific templates → `src/web/cpu/templates/`
   - GPU-specific templates → `src/web/gpu/templates/`

**Success Criteria**:
- Markdown rendering is in common (shared by both modes)
- Static assets are in common
- Mode-specific templates are separated
- Template loading works for both modes

**Files Created/Modified**:
- `src/common/web/docs.py` (moved from `src/web/docs.py`)
- `src/common/web/static/` (shared assets)
- `src/web/cpu/templates/` (CPU templates)
- `src/web/gpu/templates/` (GPU templates)

**Files Deleted**:
- `src/web/docs.py`

---

#### Step 6: Extract CPU-Specific Implementation
**Estimated Effort**: 4 hours

1. **Create `src/api/cpu/runtime.py`**:
   - Migrate logic from `src/modes/sqlite_only/runtime.py`
   - Implement `build_cpu_runtime(config, db, worker_control) -> ModeRuntime`
   - Return runtime with SQLite-only search provider

2. **Create `src/api/cpu/search_provider.py`**:
   - Migrate `src/search/sqlite_provider.py` here
   - Implement `SQLiteSearchProvider` conforming to `SearchProvider` protocol

3. **Create `src/api/cpu/embedding_service.py`** (optional):
   - Create no-op implementation of `EmbeddingProvider` for CPU mode
   - Or leave as None and handle gracefully in services

**Success Criteria**:
- CPU runtime can be built independently
- CPU runtime uses only SQLite search
- No GPU dependencies in CPU code path
- Conforms to protocols defined in Step 2

**Files Created**:
- `src/api/cpu/runtime.py`
- `src/api/cpu/search_provider.py`
- `src/api/cpu/embedding_service.py` (optional)

---

#### Step 7: Migrate API Services for CPU
**Estimated Effort**: 3 hours

1. **Move services to `src/api/services/`**:
   - Copy relevant service files from `src/services/`
   - Update to use strategy pattern

2. **Update `search_service.py`**:
   - Use `SearchProvider` protocol
   - Support CPU search provider

3. **Update other services** (settings, operations, worker_control, telemetry):
   - Update imports to use `common/`
   - Ensure CPU compatibility

**Success Criteria**:
- Services depend on protocols, not concrete implementations
- Services work with CPU implementation
- All imports point to new structure

**Files Created**:
- `src/api/services/search_service.py`
- `src/api/services/settings_service.py`
- `src/api/services/operations_service.py`
- `src/api/services/worker_control.py`
- `src/api/services/telemetry_service.py`

---

#### Step 8: Consolidate API Server for CPU
**Estimated Effort**: 3 hours

1. **Move API server entry point**:
   - `src/api_server.py` → `src/api/server.py`
   - Update all imports to new structure

2. **Update runtime builder in `server.py`**:
   ```python
   from src.common.config.config import get_config
   from src.common.interfaces.runtime import ModeRuntime

   def build_runtime(config, db, worker_control) -> ModeRuntime:
       if config.is_cpu_only():
           from src.api.cpu.runtime import build_cpu_runtime
           return build_cpu_runtime(config, db, worker_control)
       else:
           # Will implement in Phase 2
           raise NotImplementedError("GPU mode not yet migrated")
   ```

3. **Update routers**:
   - Update all imports in `src/api/routers/`
   - Use new service paths
   - Use CPU templates from `web/cpu/`

4. **Update dependencies.py**:
   - Use dependency injection for runtime selection
   - Expose providers through FastAPI dependencies

**Success Criteria**:
- API server starts successfully in CPU mode
- All endpoints work correctly
- Web UI renders using CPU templates
- No references to old paths

**Files Created/Modified**:
- `src/api/server.py` (moved from `src/api_server.py`)
- Update `src/api/dependencies.py`
- Update all files in `src/api/routers/`

**Files Deleted**:
- `src/api_server.py`

---

#### Step 9: Test CPU Implementation
**Estimated Effort**: 2 hours

1. **Set environment**: `CHL_SEARCH_MODE=sqlite_only`
2. **Start API server**: `python -m src.api.server`
3. **Test all endpoints**:
   - Health check
   - Categories
   - Entries (create, read, update)
   - Search (SQLite text search)
   - Settings
   - Operations
4. **Test web UI**:
   - All pages load
   - Forms work
   - Settings display correctly
5. **Run existing tests** (if any)

**Success Criteria**:
- ✅ API server starts without errors
- ✅ All endpoints return correct responses
- ✅ SQLite search works correctly
- ✅ Web UI is fully functional
- ✅ No import errors or missing modules
- ✅ All tests pass

**CHECKPOINT**: Do not proceed to Phase 2 until CPU implementation is fully validated.

---

### PHASE 2: GPU Implementation

#### Step 10: Extract GPU-Specific Implementation
**Estimated Effort**: 5 hours

1. **Create `src/api/gpu/embedding_client.py`**:
   - Migrate `src/embedding/client.py` here
   - Rename to `GPUEmbeddingClient`
   - Implement `EmbeddingProvider` protocol

2. **Create `src/api/gpu/reranker_client.py`**:
   - Migrate `src/embedding/reranker.py` here
   - Keep as GPU-specific implementation

3. **Create `src/api/gpu/faiss_manager.py`**:
   - Migrate `src/search/faiss_index.py` and `src/search/thread_safe_faiss.py` here
   - Keep FAISS logic isolated to GPU path

4. **Create `src/api/gpu/search_provider.py`**:
   - Migrate `src/search/vector_provider.py` here
   - Implement `SearchProvider` protocol

5. **Create `src/api/gpu/runtime.py`**:
   - Migrate logic from `src/modes/vector/runtime.py`
   - Implement `build_gpu_runtime(config, db, worker_control) -> ModeRuntime`
   - Wire up GPU-specific components

**Success Criteria**:
- GPU runtime can be built independently
- GPU code isolated from CPU code
- All GPU dependencies (llama-cpp-python, faiss) only in gpu/ folder
- Implements same protocols as CPU

**Files Created**:
- `src/api/gpu/embedding_client.py`
- `src/api/gpu/reranker_client.py`
- `src/api/gpu/faiss_manager.py`
- `src/api/gpu/search_provider.py`
- `src/api/gpu/runtime.py`

---

#### Step 11: Update API Services for GPU
**Estimated Effort**: 3 hours

1. **Update `embedding_service.py`**:
   - Add factory function to create GPU embedding provider
   - Inject `EmbeddingProvider` instead of concrete `EmbeddingClient`

2. **Update `background_worker.py`**:
   - Accept `EmbeddingProvider` instead of `EmbeddingClient`
   - Support both CPU (no-op) and GPU implementations

3. **Update `search_service.py`**:
   - Ensure it works with GPU vector provider
   - Support fallback from vector to SQLite

**Success Criteria**:
- Services work with both CPU and GPU implementations
- Runtime selection is based on config
- No conditional imports in service code

**Files Modified**:
- `src/api/services/embedding_service.py`
- `src/api/services/search_service.py`
- `src/api/services/background_worker.py`

---

#### Step 12: Integrate GPU Runtime into API Server
**Estimated Effort**: 2 hours

1. **Update `src/api/server.py`**:
   ```python
   def build_runtime(config, db, worker_control) -> ModeRuntime:
       if config.is_cpu_only():
           from src.api.cpu.runtime import build_cpu_runtime
           return build_cpu_runtime(config, db, worker_control)
       else:
           from src.api.gpu.runtime import build_gpu_runtime
           return build_gpu_runtime(config, db, worker_control)
   ```

2. **Update routers** (if needed):
   - Ensure routers work with both CPU and GPU modes
   - Use GPU templates from `web/gpu/` when in GPU mode

**Success Criteria**:
- API server can select runtime based on config
- Both CPU and GPU modes work
- Template selection is mode-aware

**Files Modified**:
- `src/api/server.py`
- Relevant routers if template paths need updating

---

#### Step 13: Test GPU Implementation
**Estimated Effort**: 2 hours

1. **Set environment**: `CHL_SEARCH_MODE=auto` (or remove to use default)
2. **Start API server**: `python -m src.api.server`
3. **Test all endpoints** (same as Step 9):
   - Health check (should show FAISS status)
   - Categories
   - Entries (create, read, update)
   - Search (vector/FAISS search with reranking)
   - Settings
   - Operations (including index rebuild)
4. **Test web UI** with GPU templates
5. **Test embedding worker** background processing

**Success Criteria**:
- ✅ API server starts without errors
- ✅ FAISS index loads or initializes
- ✅ Vector search works correctly
- ✅ Reranking works correctly
- ✅ Background embedding worker processes queue
- ✅ Web UI uses GPU templates
- ✅ All tests pass

**CHECKPOINT**: Do not proceed to Phase 3 until GPU implementation is fully validated.

---

### PHASE 3: MCP Server Isolation

#### Step 14: Isolate MCP Server
**Estimated Effort**: 3 hours

1. **Move MCP entry point**:
   - `src/server.py` → `src/mcp/server.py`
   - Update all imports to new structure

2. **Audit MCP dependencies**:
   - Should only import:
     - `src.common.config.config`
     - `src.common.models` (for DTOs)
   - Should NOT import:
     - Anything from `src.api/`
     - `src.embedding` (old, should be deleted)
     - `src.search` (old, should be deleted)

3. **Update `api_client.py`**:
   - Ensure it's a pure HTTP client
   - No direct database or storage access
   - Update base URL to point to API server

4. **Update all MCP handlers**:
   - Update imports to use `common/`
   - Ensure all operations go through HTTP API

**Success Criteria**:
- MCP server can be imported and started
- MCP only imports from `common/`
- No accidental imports from API server code
- HTTP client is properly configured

**Files Created/Modified**:
- `src/mcp/server.py` (moved from `src/server.py`)
- Update `src/mcp/api_client.py`
- Update `src/mcp/handlers_entries.py`
- Update `src/mcp/handlers_guidelines.py`
- Update `src/mcp/errors.py`
- Update `src/mcp/utils.py`
- Update `src/mcp/models.py`

**Files Deleted**:
- `src/server.py`

---

#### Step 15: Test MCP Server
**Estimated Effort**: 2 hours

1. **Prerequisites**:
   - API server must be running (either CPU or GPU mode)
   - Set `CHL_API_BASE_URL=http://localhost:8000`

2. **Start MCP server**: `python -m src.mcp.server`

3. **Test all MCP tools**:
   - `list_categories`
   - `read_entries` (with query and ids)
   - `write_entry` (experience and manual)
   - `update_entry`
   - `get_guidelines`

4. **Test error handling**:
   - API server down
   - Invalid parameters
   - Network errors

**Success Criteria**:
- ✅ MCP server starts without errors
- ✅ Connects to API server successfully
- ✅ All tools work correctly
- ✅ Error handling is graceful
- ✅ No direct database access
- ✅ All operations go through HTTP

**CHECKPOINT**: MCP server fully isolated and functional.

---

### PHASE 4: Cleanup

#### Step 16: Remove Old Code
**Estimated Effort**: 2 hours

1. **Remove deprecated directories**:
   ```bash
   rm -rf src/modes/
   rm -rf src/embedding/
   rm -rf src/search/
   rm -rf src/services/
   ```

2. **Verify no references to old paths**:
   ```bash
   grep -r "from src.modes" src/
   grep -r "from src.embedding" src/
   grep -r "from src.search" src/
   grep -r "import src.embedding" src/
   # Should return no results
   ```

3. **Update import statements** if any remaining:
   - Fix any stragglers missed in earlier steps

**Success Criteria**:
- No duplicate code in old and new locations
- All imports point to new structure
- No references to deleted paths
- Both servers still work after cleanup

**Directories Deleted**:
- `src/modes/`
- `src/embedding/`
- `src/search/`
- `src/services/`

---

#### Step 17: Update Documentation
**Estimated Effort**: 3 hours

1. **Create `doc/architecture/phase_0_boundaries.md`**:
   - Document the new structure
   - Explain API/MCP/common boundaries
   - Provide import rules
   - List prohibited imports

2. **Create `doc/architecture/cpu_gpu_strategy.md`**:
   - Document CPU/GPU strategy pattern
   - Explain runtime selection
   - Provide code examples
   - Document protocol interfaces

3. **Update `README.md`**:
   - Update quick start with new entry points
   - API server: `python -m src.api.server`
   - MCP server: `python -m src.mcp.server`
   - Update development setup instructions
   - Document environment variables for mode selection

4. **Create migration guide** `doc/migration/phase_0_migration.md`:
   - Document changes from old to new structure
   - Provide import path mappings
   - List breaking changes

**Success Criteria**:
- Documentation reflects new structure
- Examples use correct paths
- Entry points are clearly documented
- Migration path is documented

**Files Created**:
- `doc/architecture/phase_0_boundaries.md`
- `doc/architecture/cpu_gpu_strategy.md`
- `doc/migration/phase_0_migration.md`
- Update `README.md`

---

#### Step 18: Update and Run Tests
**Estimated Effort**: 3 hours

1. **Update existing tests**:
   - Fix import paths in all test files
   - Update to use new entry points
   - Ensure mocks use correct paths

2. **Add boundary tests** (`tests/test_boundaries.py`):
   ```python
   def test_mcp_does_not_import_api():
       """MCP should not import anything from api/"""
       # Static analysis or import checking

   def test_cpu_does_not_import_gpu():
       """CPU implementation should not import from gpu/"""

   def test_common_has_no_runtime_deps():
       """Common should not import from api/ or mcp/"""
   ```

3. **Run full test suite**:
   - Unit tests
   - Integration tests
   - Boundary tests

4. **Add smoke tests**:
   - Test API server startup (CPU mode)
   - Test API server startup (GPU mode)
   - Test MCP server startup

**Success Criteria**:
- ✅ All existing tests updated and passing
- ✅ Boundary tests prevent future coupling
- ✅ Smoke tests verify startup works
- ✅ No import errors

**Files Created/Modified**:
- `tests/test_boundaries.py`
- Update all existing test files
- `tests/test_smoke.py` (optional)

---

## Migration Checklist

### Pre-Migration
- [ ] Back up current codebase (git commit on clean branch)
- [ ] Ensure all existing tests pass
- [ ] Document current entry points and commands

### Phase 1: Foundation & CPU (Steps 1-9)
- [ ] Step 1: Create directory structure
- [ ] Step 2: Define common interfaces
- [ ] Step 3: Migrate configuration to common
- [ ] Step 4: Migrate storage to common
- [ ] Step 5: Migrate web rendering to common
- [ ] Step 6: Extract CPU-specific implementation
- [ ] Step 7: Migrate API services for CPU
- [ ] Step 8: Consolidate API server for CPU
- [ ] **Step 9: Test CPU implementation** ✅ CHECKPOINT

### Phase 2: GPU (Steps 10-13)
- [ ] Step 10: Extract GPU-specific implementation
- [ ] Step 11: Update API services for GPU
- [ ] Step 12: Integrate GPU runtime into API server
- [ ] **Step 13: Test GPU implementation** ✅ CHECKPOINT

### Phase 3: MCP (Steps 14-15)
- [ ] Step 14: Isolate MCP server
- [ ] **Step 15: Test MCP server** ✅ CHECKPOINT

### Phase 4: Cleanup (Steps 16-18)
- [ ] Step 16: Remove old code
- [ ] Step 17: Update documentation
- [ ] Step 18: Update and run tests

### Final Validation
- [ ] API server starts in CPU mode (`CHL_SEARCH_MODE=sqlite_only`)
- [ ] API server starts in GPU mode (`CHL_SEARCH_MODE=auto`)
- [ ] MCP server connects to API server
- [ ] All API endpoints work in both modes
- [ ] All MCP tools work correctly
- [ ] Web UI works in both modes
- [ ] All tests pass
- [ ] Documentation is complete and accurate
- [ ] No old code or directories remain
- [ ] No import errors or circular dependencies

## Import Rules (Boundaries)

### Allowed Imports
```python
# MCP can import from common
from src.common.config.config import Config
from src.common.models.domain import Experience

# API can import from common
from src.common.config.config import get_config
from src.common.storage.database import Database

# API CPU can import CPU-specific code
from src.api.cpu.runtime import build_cpu_runtime

# API GPU can import GPU-specific code
from src.api.gpu.runtime import build_gpu_runtime

# API services can import from common interfaces
from src.common.interfaces.embedding import EmbeddingProvider
```

### Prohibited Imports
```python
# ❌ MCP cannot import from API
from src.api.services.search_service import SearchService  # FORBIDDEN

# ❌ CPU cannot import from GPU
from src.api.gpu.embedding_client import GPUEmbeddingClient  # FORBIDDEN

# ❌ GPU cannot import from CPU (except for testing)
from src.api.cpu.runtime import build_cpu_runtime  # FORBIDDEN

# ❌ Common cannot import from API or MCP
from src.api.services import anything  # FORBIDDEN
from src.mcp.api_client import APIClient  # FORBIDDEN
```

## Estimated Timeline

- **Total Effort**: ~32 hours (4-5 days for one developer)
- **Critical Path**: Must complete phases sequentially with validation at each checkpoint

### Breakdown by Phase
| Phase | Steps | Description | Hours |
|-------|-------|-------------|-------|
| 1 | 1-9 | Foundation & CPU Implementation | 14 |
| 2 | 10-13 | GPU Implementation | 10 |
| 3 | 14-15 | MCP Server Isolation | 5 |
| 4 | 16-18 | Cleanup & Documentation | 8 |
| **Total** | **18** | | **37** |

### Detailed Breakdown
| Step | Description | Hours |
|------|-------------|-------|
| 1 | Create directory structure | 0.5 |
| 2 | Define common interfaces | 2 |
| 3 | Migrate configuration | 1 |
| 4 | Migrate storage | 1 |
| 5 | Migrate web rendering | 1 |
| 6 | Extract CPU implementation | 4 |
| 7 | Migrate API services for CPU | 3 |
| 8 | Consolidate API server for CPU | 3 |
| 9 | **Test CPU** (CHECKPOINT) | 2 |
| 10 | Extract GPU implementation | 5 |
| 11 | Update API services for GPU | 3 |
| 12 | Integrate GPU runtime | 2 |
| 13 | **Test GPU** (CHECKPOINT) | 2 |
| 14 | Isolate MCP server | 3 |
| 15 | **Test MCP** (CHECKPOINT) | 2 |
| 16 | Remove old code | 2 |
| 17 | Update documentation | 3 |
| 18 | Update and run tests | 3 |
| **Total** | | **37** |

## Risks and Mitigations

### Risk 1: Import Circular Dependencies
**Risk**: New structure might create circular imports between common/api/mcp

**Mitigation**:
- Define clear dependency hierarchy: `mcp` → `common` ← `api`
- Never import from `api/` in `common/` or `mcp/`
- Use dependency injection and protocols
- Add boundary tests to catch violations

### Risk 2: Breaking Changes
**Risk**: Full cutover means all paths change at once

**Mitigation**:
- Phase-based approach with validation checkpoints
- Test thoroughly at each checkpoint before proceeding
- Keep old code until new code is validated
- Comprehensive import updates in each step

### Risk 3: Complex Refactoring Scope
**Risk**: This is extensive refactoring across entire codebase

**Mitigation**:
- Follow steps sequentially, commit after each step
- Run tests after each phase
- Three checkpoints ensure quality (CPU, GPU, MCP)
- Detailed success criteria for each step

### Risk 4: Template/Web UI Complexity
**Risk**: Splitting templates between CPU/GPU modes might cause rendering issues

**Mitigation**:
- Keep shared rendering logic in `common/web/docs.py`
- Only split HTML templates, not logic
- Test web UI at each checkpoint
- Document template selection strategy

## Success Criteria

Phase 0 is complete when:

1. **Separation Achieved**:
   - [ ] API server code in `src/api/` with `cpu/` and `gpu/` subdirectories
   - [ ] MCP server code in `src/mcp/`
   - [ ] Shared code in `src/common/`
   - [ ] Web templates split between `web/cpu/` and `web/gpu/`
   - [ ] No cross-contamination between API and MCP

2. **CPU/GPU Isolation**:
   - [ ] CPU implementation in `src/api/cpu/`
   - [ ] GPU implementation in `src/api/gpu/`
   - [ ] Clear protocols/interfaces between them
   - [ ] Runtime selection works correctly
   - [ ] No `__init__.py` files

3. **Entry Points Updated**:
   - [ ] API server: `python -m src.api.server`
   - [ ] MCP server: `python -m src.mcp.server`
   - [ ] No backward compatibility symlinks
   - [ ] All documentation updated with new paths

4. **Functional Validation** (All Checkpoints Passed):
   - [ ] ✅ CPU mode fully functional (Checkpoint 1)
   - [ ] ✅ GPU mode fully functional (Checkpoint 2)
   - [ ] ✅ MCP server fully functional (Checkpoint 3)
   - [ ] All endpoints work in both modes
   - [ ] All tools work
   - [ ] Tests pass
   - [ ] No regressions

5. **Cleanup Complete**:
   - [ ] Old directories removed (`modes/`, `embedding/`, `search/`, `services/`)
   - [ ] No duplicate code
   - [ ] All imports point to new structure
   - [ ] Boundary tests prevent future coupling

6. **Documentation Complete**:
   - [ ] Architecture boundaries documented
   - [ ] Import rules documented
   - [ ] CPU/GPU strategy documented
   - [ ] Migration guide complete
   - [ ] README updated with new entry points

## Next Steps (Phase A Preview)

After Phase 0 is complete, Phase A will focus on:
- Finalizing `requirements_cpu.txt` for API server CPU mode
- Finalizing `requirements_gpu.txt` for API server GPU mode (per hardware: Metal, CUDA, ROCm, oneAPI)
- Creating per-platform installation guides
- Documenting the "API venv per platform + MCP via uv sync" workflow
- Ensuring dependencies are correctly isolated based on Phase 0 structure

Phase 0's clean separation makes Phase A straightforward since CPU and GPU dependencies are already isolated in separate directories.
