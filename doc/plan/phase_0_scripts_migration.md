# Phase 0: Scripts Migration Guide

## Overview

This document details the migration strategy for 13+ operational scripts in the `scripts/` directory as part of Phase 0 codebase isolation. The goal is to transition scripts from direct API internals access to HTTP-based orchestration while supporting mode-aware operations.

## Migration Principles

### 1. **HTTP-First Architecture**
- **Preferred**: Scripts call API endpoints via `CHLAPIClient` from `src.common.api_client.client`
- **Avoid**: Direct imports from `src.api.*` (violates separation of concerns)
- **Exceptions**: Setup/testing scripts that configure internal components

### 2. **Mode-Aware Orchestration**
- Scripts detect runtime mode (CPU/GPU) via API endpoints (e.g., `GET /settings`)
- Scripts trigger mode-specific operations when needed (e.g., embeddings sync for GPU)
- Common utilities (e.g., spreadsheet reading, DB access) remain in `src.common.*`

### 3. **Shared Utilities**
- Low-level operations live in `src.common/storage/` (DB, schema, sheets_client)
- Scripts compose common utilities + HTTP calls for higher-level workflows

## Global Import Updates

**All scripts must update these imports:**

```python
# ❌ Old imports
from src.api_client import CHLAPIClient
from src.config import Config
from src.storage.database import Database
from src.storage.repository import Repository
from src.storage.sheets_client import GoogleSheetsClient

# ✅ New imports
from src.common.api_client.client import CHLAPIClient
from src.common.config.config import Config
from src.common.storage.database import Database
from src.common.storage.repository import Repository
from src.common.storage.sheets_client import GoogleSheetsClient
```

## Per-Script Migration Strategy

### 1. `scripts/import.py` - Mode-Aware Import

**Current Behavior:**
- CPU mode: Read spreadsheet → Write to SQLite
- GPU mode: Read spreadsheet → Write to SQLite → Spawn embedding job

**Migration Strategy: HTTP + Mode Detection**

```python
from src.common.api_client.client import CHLAPIClient
from src.common.storage.sheets_client import GoogleSheetsClient
from src.common.config.config import Config

def import_from_sheets():
    config = Config()
    sheets = GoogleSheetsClient()
    api_client = CHLAPIClient(base_url=config.api_url)

    # Step 1: Read from spreadsheet (common utility)
    entries = sheets.read_entries()

    # Step 2: Import entries via API (mode-agnostic)
    for entry in entries:
        api_client.post("/entries", json=entry)

    # Step 3: Detect mode and trigger GPU operations if needed
    settings = api_client.get("/settings")
    if settings["search_mode"] == "auto":
        # Trigger embedding sync (GPU-specific operation)
        job = api_client.post("/operations/sync-embeddings")
        job_id = job["id"]

        # Optional: Poll for completion
        while True:
            status = api_client.get(f"/operations/{job_id}")
            if status["state"] in ["completed", "failed"]:
                break
            time.sleep(1)

        print(f"Embeddings sync: {status['state']}")
```

**Key Points:**
- ✅ Uses common utilities for spreadsheet reading
- ✅ HTTP-only communication with API server
- ✅ Mode detection via `/settings` endpoint
- ✅ GPU operations triggered conditionally
- ❌ No direct imports from `src.api.*`

**Transaction Semantics:**
- For GPU mode, the import is "transactional" in the sense that if embedding sync fails, the user is notified
- The script can optionally wait for embedding completion or return immediately (async pattern)

---

### 2. `scripts/export.py` - Pure HTTP Export

**Current Behavior:**
- Read entries from DB → Write to spreadsheet (mode-agnostic)

**Migration Strategy: Pure HTTP**

```python
from src.common.api_client.client import CHLAPIClient
from src.common.storage.sheets_client import GoogleSheetsClient
from src.common.config.config import Config

def export_to_sheets():
    config = Config()
    sheets = GoogleSheetsClient()
    api_client = CHLAPIClient(base_url=config.api_url)

    # Step 1: Fetch entries via API (mode-agnostic)
    entries = api_client.get("/entries")

    # Step 2: Write to spreadsheet (common utility)
    sheets.write_entries(entries)
```

**Key Points:**
- ✅ Fully mode-agnostic (works for CPU and GPU)
- ✅ HTTP-only communication
- ✅ Simple read operation, no mode detection needed

---

### 3. `scripts/seed_default_content.py` - Orchestrator Script

**Current Behavior:**
- Imports from `src.config`
- Dynamically loads `setup-gpu.py` or `setup-cpu.py`

**Migration Strategy: HTTP + Subprocess**

```python
import subprocess
from src.common.config.config import Config
from src.common.api_client.client import CHLAPIClient

def seed_content():
    config = Config()
    api_client = CHLAPIClient(base_url=config.api_url)

    # Detect mode
    settings = api_client.get("/settings")
    mode = settings["search_mode"]

    # Call appropriate setup script via subprocess (not dynamic import)
    if mode == "auto":
        subprocess.run(["python", "scripts/setup-gpu.py"], check=True)
    else:
        subprocess.run(["python", "scripts/setup-cpu.py"], check=True)

    # Seed default entries via HTTP
    api_client.post("/admin/seed-defaults")
```

**Key Points:**
- ✅ No dynamic imports from `src.api.*`
- ✅ Uses subprocess to delegate to setup scripts
- ✅ Orchestrates via HTTP

---

### 4. `scripts/rebuild_index.py` - Index Rebuild

**Current Behavior:**
- Rebuilds FAISS index (GPU mode) or SQLite FTS (CPU mode)

**Migration Strategy: HTTP**

```python
from src.common.api_client.client import CHLAPIClient
from src.common.config.config import Config

def rebuild_index():
    config = Config()
    api_client = CHLAPIClient(base_url=config.api_url)

    # Trigger rebuild (mode-aware on server side)
    job = api_client.post("/operations/rebuild-index")
    job_id = job["id"]

    # Poll for completion
    while True:
        status = api_client.get(f"/operations/{job_id}")
        if status["state"] in ["completed", "failed"]:
            break
        time.sleep(1)

    print(f"Index rebuild: {status['state']}")
```

**Key Points:**
- ✅ API server handles mode detection
- ✅ HTTP-only communication
- ✅ No imports from `src.api.*`

---

### 5. `scripts/sync_embeddings.py` - GPU Embeddings Sync

**Current Behavior:**
- Syncs embeddings for all entries (GPU-only operation)

**Migration Strategy: HTTP**

```python
from src.common.api_client.client import CHLAPIClient
from src.common.config.config import Config

def sync_embeddings():
    config = Config()
    api_client = CHLAPIClient(base_url=config.api_url)

    # Trigger sync (GPU-only endpoint)
    job = api_client.post("/operations/sync-embeddings")
    job_id = job["id"]

    # Poll for completion
    while True:
        status = api_client.get(f"/operations/{job_id}")
        if status["state"] in ["completed", "failed"]:
            break
        time.sleep(1)

    print(f"Embeddings sync: {status['state']}")
```

**Key Points:**
- ✅ GPU-specific operation, but invoked via HTTP
- ✅ No imports from `src.api.gpu.*`

---

### 6. `scripts/sync_guidelines.py` - Guidelines Sync

**Current Behavior:**
- Syncs guidelines from external source (mode-agnostic)

**Migration Strategy: HTTP**

```python
from src.common.api_client.client import CHLAPIClient
from src.common.config.config import Config

def sync_guidelines():
    config = Config()
    api_client = CHLAPIClient(base_url=config.api_url)

    # Trigger sync
    job = api_client.post("/operations/sync-guidelines")
    job_id = job["id"]

    # Poll for completion
    while True:
        status = api_client.get(f"/operations/{job_id}")
        if status["state"] in ["completed", "failed"]:
            break
        time.sleep(1)

    print(f"Guidelines sync: {status['state']}")
```

---

### 7. `scripts/setup-gpu.py` - GPU Environment Setup

**Current Behavior:**
- Downloads models, initializes FAISS, configures GPU environment
- Tightly coupled to GPU internals

**Migration Strategy: Keep API Imports (EXCEPTION)**

```python
# ✅ Exception: Setup scripts can import from src.api.*
from src.api.gpu.embedding_service import EmbeddingService
from src.common.storage.database import Database
from src.common.config.config import Config

def setup_gpu():
    config = Config()
    db = Database(config.db_path)

    # Direct access to GPU components for setup
    embedding_service = EmbeddingService(config)
    embedding_service.download_models()
    embedding_service.initialize_faiss()

    # Verify setup
    assert embedding_service.is_available()
```

**Rationale:**
- ⚠️ One-time setup, tightly coupled to GPU internals
- ⚠️ Exception to the "no src.api.* imports" rule
- ⚠️ Runs before API server is available
- ✅ Clearly documented as setup-only script

---

### 8. `scripts/setup-cpu.py` - CPU Environment Setup

**Current Behavior:**
- Initializes SQLite FTS, seeds database

**Migration Strategy: HTTP + Common**

```python
from src.common.storage.database import Database
from src.common.storage.repository import Repository
from src.common.config.config import Config
from src.common.api_client.client import CHLAPIClient

def setup_cpu():
    config = Config()
    db = Database(config.db_path)
    repo = Repository(db)

    # Initialize DB schema
    repo.initialize_schema()

    # Verify setup via API (if running)
    try:
        api_client = CHLAPIClient(base_url=config.api_url)
        health = api_client.get("/health")
        print(f"API health: {health}")
    except Exception:
        print("API not running, setup complete (start API to verify)")
```

**Key Points:**
- ✅ Uses common utilities for DB setup
- ✅ HTTP for validation (optional)
- ✅ No imports from `src.api.*`

---

### 9. `scripts/gpu_smoke_test.py` - GPU Internal Testing

**Current Behavior:**
- Tests GPU components (embedding, FAISS, reranking) directly

**Migration Strategy: Keep API Imports (EXCEPTION)**

```python
# ✅ Exception: Test scripts can import from src.api.*
from src.api.gpu.embedding_service import EmbeddingService
from src.api.gpu.embedding_client import EmbeddingClient
from src.api.gpu.reranker_client import RerankerClient
from src.api.gpu.faiss_manager import FAISSManager
from src.common.config.config import Config

def test_gpu():
    config = Config()

    # Test embedding service
    emb_service = EmbeddingService(config)
    assert emb_service.is_available()

    # Test embedding client
    emb_client = EmbeddingClient(config)
    embedding = emb_client.encode_single("test")
    assert len(embedding) == 384

    # Test reranker
    reranker = RerankerClient(config)
    scores = reranker.rerank("query", ["doc1", "doc2"])
    assert len(scores) == 2
```

**Rationale:**
- ⚠️ Tests internal GPU components, not HTTP API
- ⚠️ Exception to the "no src.api.* imports" rule
- ✅ Clearly documented as testing-only script

---

### 10. `scripts/search_health.py` - Search Health Diagnostic

**Current Behavior:**
- Checks search system health (mode-agnostic)

**Migration Strategy: HTTP**

```python
from src.common.api_client.client import CHLAPIClient
from src.common.config.config import Config

def check_search_health():
    config = Config()
    api_client = CHLAPIClient(base_url=config.api_url)

    # Get health status
    health = api_client.get("/search/health")

    # Get current mode
    settings = api_client.get("/settings")
    mode = settings["search_mode"]

    print(f"Search mode: {mode}")
    print(f"Search health: {health}")
```

---

### 11. `scripts/tweak/read.py` - Low-Level DB Read

**Current Behavior:**
- Reads raw DB entries for debugging

**Migration Strategy: Common + HTTP**

```python
from src.common.storage.repository import Repository
from src.common.storage.database import Database
from src.common.api_client.client import CHLAPIClient
from src.common.config.config import Config

def read_entry(entry_id: str):
    config = Config()

    # Direct DB read for low-level debugging
    db = Database(config.db_path)
    repo = Repository(db)
    entry = repo.get_entry(entry_id)

    # Also fetch via API for comparison
    api_client = CHLAPIClient(base_url=config.api_url)
    api_entry = api_client.get(f"/entries/{entry_id}")

    print("DB entry:", entry)
    print("API entry:", api_entry)
```

**Key Points:**
- ✅ Uses common utilities for direct DB access
- ✅ HTTP for comparison/validation
- ✅ Low-level diagnostic tool

---

### 12. `scripts/tweak/write.py` - Low-Level DB Write

**Current Behavior:**
- Writes raw DB entries for debugging/maintenance

**Migration Strategy: Common + HTTP**

```python
from src.common.storage.repository import Repository
from src.common.storage.database import Database
from src.common.api_client.client import CHLAPIClient
from src.common.config.config import Config

def write_entry(entry_data: dict):
    config = Config()
    db = Database(config.db_path)
    repo = Repository(db)

    # Direct DB write
    repo.upsert_entry(entry_data)

    # Trigger embeddings sync if GPU mode
    api_client = CHLAPIClient(base_url=config.api_url)
    settings = api_client.get("/settings")
    if settings["search_mode"] == "auto":
        api_client.post("/operations/sync-embeddings")
```

**Key Points:**
- ✅ Direct DB write for low-level operations
- ✅ Mode-aware embedding sync via HTTP
- ✅ Maintenance tool

---

## Migration Summary Table

| Script | Strategy | Imports from src.api.* | HTTP Calls | Mode Detection |
|--------|----------|------------------------|------------|----------------|
| import.py | HTTP + Mode Detection | ❌ | ✅ | ✅ (via /settings) |
| export.py | Pure HTTP | ❌ | ✅ | ❌ (mode-agnostic) |
| seed_default_content.py | HTTP + Subprocess | ❌ | ✅ | ✅ |
| rebuild_index.py | HTTP | ❌ | ✅ | ❌ (server-side) |
| sync_embeddings.py | HTTP | ❌ | ✅ | ❌ (GPU-only) |
| sync_guidelines.py | HTTP | ❌ | ✅ | ❌ |
| **setup-gpu.py** | **Keep API Imports** | ✅ (exception) | ❌ | ❌ |
| setup-cpu.py | HTTP + Common | ❌ | ✅ (optional) | ❌ |
| **gpu_smoke_test.py** | **Keep API Imports** | ✅ (exception) | ❌ | ❌ |
| search_health.py | HTTP | ❌ | ✅ | ✅ |
| tweak/read.py | Common + HTTP | ❌ | ✅ (comparison) | ❌ |
| tweak/write.py | Common + HTTP | ❌ | ✅ (embeddings) | ✅ |

## Exception Policy

**Scripts allowed to import from `src.api.*`:**
1. `setup-gpu.py` - Initial GPU environment setup (runs before API server)
2. `gpu_smoke_test.py` - Internal GPU component testing

**Rationale:**
- These scripts configure/test internal components during initial environment setup
- They run before or independently of the API server
- Clearly documented as setup/testing tools, not operational scripts

**All other scripts must use:**
- HTTP via `CHLAPIClient` for orchestration
- Common utilities from `src.common.*` for low-level operations

## Testing Checklist

After migration, verify each script:

- [ ] `python scripts/import.py` - CPU mode (no embeddings)
- [ ] `python scripts/import.py` - GPU mode (triggers embeddings)
- [ ] `python scripts/export.py` - Both modes
- [ ] `python scripts/seed_default_content.py` - Both modes
- [ ] `python scripts/rebuild_index.py` - Both modes
- [ ] `python scripts/sync_embeddings.py` - GPU mode only
- [ ] `python scripts/sync_guidelines.py` - Both modes
- [ ] `python scripts/setup-gpu.py` - Standalone (no API)
- [ ] `python scripts/setup-cpu.py` - Standalone (no API)
- [ ] `python scripts/gpu_smoke_test.py` - Standalone (no API)
- [ ] `python scripts/search_health.py` - Both modes (API running)
- [ ] `python scripts/tweak/read.py <id>` - Both modes
- [ ] `python scripts/tweak/write.py <data>` - Both modes

## API Endpoints Required

Scripts depend on these API endpoints (ensure they exist):

- `GET /settings` - Get current configuration (including search_mode)
- `GET /entries` - List all entries
- `POST /entries` - Create entry
- `GET /entries/{id}` - Get single entry
- `POST /operations/sync-embeddings` - Trigger embedding sync (GPU)
- `POST /operations/rebuild-index` - Trigger index rebuild
- `POST /operations/sync-guidelines` - Trigger guidelines sync
- `GET /operations/{id}` - Get operation status
- `GET /search/health` - Get search system health
- `GET /health` - Get API health
- `POST /admin/seed-defaults` - Seed default content

## Import Validation

Add to `tests/architecture/test_boundaries.py`:

```python
def test_scripts_no_api_imports():
    """Ensure operational scripts don't import from src.api.*"""
    exceptions = ["setup-gpu.py", "gpu_smoke_test.py"]  # Setup/test scripts

    for script_path in glob("scripts/**/*.py"):
        if any(exc in script_path for exc in exceptions):
            continue  # Skip exceptions

        with open(script_path) as f:
            content = f.read()
            assert "from src.api." not in content, \
                f"{script_path} imports from src.api.* (use src.common.api_client instead)"
            assert "import src.api." not in content, \
                f"{script_path} imports src.api.* (use src.common.api_client instead)"
```

---

## Migration Order

Suggested order for script migration (Step 19 of Phase 4):

1. **Foundation**: Update global imports in all scripts (config, storage, api_client)
2. **Mode-agnostic scripts**: export.py, sync_guidelines.py, search_health.py
3. **HTTP-only scripts**: rebuild_index.py, sync_embeddings.py
4. **Mode-aware scripts**: import.py, seed_default_content.py, tweak/write.py
5. **Common utilities scripts**: setup-cpu.py, tweak/read.py
6. **Exception scripts (last)**: setup-gpu.py, gpu_smoke_test.py (keep api imports)
7. **Test all scripts** per checklist above

---

**Document Version**: 1.0
**Last Updated**: Phase 0 Planning
**Related**: `phase_0_code_isolation_v2.md`, `architecture_refine.md`
