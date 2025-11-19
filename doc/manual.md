# CHL Operator's Manual

This manual covers the setup, daily workflows, and operational tasks for the Curated Heuristic Loop (CHL) system. For the project's philosophy, see [concept.md](./concept.md), and for technical details, see [architecture.md](./architecture.md).

## 1. Initial Setup

This section guides you through the first-time setup of the CHL environment.

### 1.1. Quick Start
For the fastest setup, please follow the **Quick Start** guide in the main [README.md](../README.md). It will guide you through installing dependencies and starting the web server. The rest of this manual assumes you have completed those steps.

**Choose your installation mode:**
- **GPU mode** (recommended for semantic search): Install with `--extra ml` for semantic search using FAISS and embeddings. Requires ≥8 GB VRAM. Enables conceptual query matching (e.g., "best practices" matches "recommended approaches").
- **CPU-only mode** (keyword search): Install without ML extras for keyword search using SQLite text matching. Use when GPU resources are unavailable or when literal keyword matching is sufficient for your use case.

**Decision guidance:**
- Have ≥8 GB VRAM and need semantic search? → GPU mode
- Limited VRAM or keyword search is sufficient? → CPU-only mode
- Can switch modes later, but FAISS snapshots are NOT portable between modes

See the [CPU-Only Mode](#9-cpu-only-mode) section below for details on running CHL without ML dependencies.

### 1.2. First-Time Setup Script
The setup scripts initialize your local environment. Choose the appropriate script based on your setup:
- `setup-gpu.py`: For GPU-enabled systems with vector search (downloads ML models)
- `setup-cpu.py`: For CPU-only systems using SQLite keyword search (no ML dependencies)

**Command (GPU mode):**
```bash
uv run python scripts/setup-gpu.py
```

**What GPU setup does:**
1. Creates the `data/` directory structure
2. Initializes the SQLite database (`chl.db`)
3. Downloads the required embedding and reranker models
4. Creates the FAISS index directory
5. Validates model availability

**Command (CPU-only mode):**
```bash
python scripts/setup-cpu.py
```

**What CPU-only setup does:**
1. Creates the `data/` directory (no FAISS directory)
3. Initializes the SQLite database (`chl.db`)
4. Seeds default categories and sample entries
5. Validates credential paths (non-fatal if missing)

**When to use:**
- After first cloning the repository
- If your `data/` directory is deleted or corrupted
- To re-download models after changing selection (GPU mode only)

### 1.3. Seed Default Content
After setup, you can seed the database with default categories and example entries.

**Command:**
```bash
uv run python scripts/seed_default_content.py
```
This command is idempotent and also syncs the `generator.md` and `evaluator.md` guidelines into the `GLN` category.

## 2. The CHL Workflow

The CHL workflow is designed for developers, AI assistants, and curators to collaborate on building a shared knowledge base.

### 2.1. End-to-End Workflow
1.  **Capture (Developer & Assistant):** During a work session, the assistant uses existing knowledge (`read_entries`). Afterwards, the assistant reflects on the session (`write_entry`), capturing new insights as either atomic experiences or updates to manuals. These new entries are saved to the local SQLite database with a `pending` status.
2.  **Vector Refresh (Operator):** To keep search fast and accurate, an operator periodically regenerates the vector index. This is done via the **Web UI** or by running `scripts/rebuild_index.py`. This process generates embeddings for all `pending` entries.
3.  **Export for Review (Curator):** A curator exports all `pending` entries from the team's local databases into a shared Google Sheet using `scripts/export.py`.
4.  **Curate (Curator):** The curator reviews the submitted entries in Google Sheets, merging duplicates, editing for clarity, and approving the highest-quality insights.
5.  **Publish (Curator):** Approved entries are moved to a "Published" tab or a separate Published Sheet.
6.  **Distribute (Developer):** Developers sync their local databases from the Published Sheet using `scripts/import.py`. This updates their local knowledge base with the latest curated heuristics.

### 2.2. MCP Interaction Flow (for Assistant developers)
1.  **Startup:** The MCP service loads its configuration and advertises available categories via `list_categories`.
2.  **Generator Mode:** The assistant queries for relevant entries using `read_entries(query=...)`.
3.  **Evaluator Mode:** The assistant writes new knowledge using `write_entry(...)`, which returns similarity scores to help decide whether to create, update, or refactor an entry.

### 2.3. Review and Governance
-   **Controlled Vocabulary:** Use a consistent set of categories, sections, and tags.
-   **Curator Actions:** Curators should record actions (e.g., `accepted`, `superseded`) in the Published Sheet to provide feedback to the system.
-   **Analytics:** Periodically run analytics on the knowledge base to identify unused entries or duplicate clusters.

## 3. Web UI Guide

The simplest way to manage CHL is through the built-in web interface, available by running `uvicorn src.api.server:app` and navigating to `http://127.0.0.1:8000`.

### 3.1. Settings Dashboard (`/settings`)
This page is for initial configuration and system management.
-   **First-Time Checklist:** Guides you through setting up credentials and sheet IDs.
-   **Configuration:** Load `scripts_config.yaml` to configure Google Sheets access.
-   **Models:** Select the embedding and reranker models.
-   **Diagnostics:** Validate connections and review audit logs.
-   **Backup/Restore:** Download or restore a JSON backup of system metadata.

### 3.2. Operations Dashboard (`/operations`)
This page is for day-to-day operational tasks.
-   **Jobs:** Trigger `import`, `export`, and `rebuild_index` jobs with a single click.
-   **Job History:** View the status and logs of recent jobs.
-   **FAISS Snapshots:** Download the current FAISS index for backup or upload a new one to quickly update the search index.

## 4. Command-Line Operations

For automation and scripting, use these command-line tools.

### 4.1. Search and Indexing
-   **Rebuild Search Index:** Regenerates embeddings and the FAISS index from scratch.
    ```bash
    uv run python scripts/rebuild_index.py
    ```
-   **Check Search Health:** Inspects the status of the search index and embeddings.
    ```bash
    uv run python scripts/search_health.py
    ```

### 4.2. Data Synchronization
Before running, ensure your `scripts/scripts_config.yaml` is configured with the correct Google Sheet IDs.

-   **Export to Google Sheets:** Writes local `pending` entries to the review sheet.
    ```bash
    uv run python scripts/export.py
    ```
-   **Import from Google Sheets:** Overwrites the local database with content from the published sheet. **This is a destructive operation.**
    ```bash
    uv run python scripts/import.py --yes
    ```

## 5. API Server Operations

The FastAPI server provides REST endpoints for advanced control.

-   **Health Check:**
    ```bash
    curl http://localhost:8000/health
    ```
-   **Queue Status:**
    ```bash
    curl http://localhost:8000/admin/queue/status
    ```
-   **Pause/Resume Workers:**
    ```bash
    curl -X POST http://localhost:8000/admin/queue/pause
    curl -X POST http://localhost:8000/admin/queue/resume
    ```
-   **Retry Failed Embeddings:**
    ```bash
    curl -X POST http://localhost:8000/admin/queue/retry-failed
    ```

## 6. Reference

### 6.1. Category Index
The system is pre-configured with the following categories. You can add more as needed.
  - `figma_page_design` (`FPD`)
  - `database_schema_design` (`DSD`)
  - `page_specification` (`PGS`)
  - `ticket_management` (`TMG`)
  - `architecture_design` (`ADG`)
  - `migration_code` (`MGC`)
  - `frontend_html` (`FTH`)
  - `laravel_php_web` (`LPW`)
  - `python_agent`(`PGT`)
  - `playwright_page_test` (`PPT`)
  - `e2e_test` (`EET`)
  - `pull_request` (`PRQ`)

### 6.2. Environment Variables
While `scripts/scripts_config.yaml` is preferred, the scripts and server can be configured with environment variables. Key variables include:
- `CHL_EXPERIENCE_ROOT` - Path to data directory
- `CHL_DATABASE_PATH` - Path to SQLite database file
- `CHL_EMBEDDING_REPO` - Embedding model repository (GPU mode only)
- `CHL_EMBEDDING_N_GPU_LAYERS` / `CHL_RERANKER_N_GPU_LAYERS` - Optional GPU offload depth for GGUF models (`0` = CPU-only, `-1` = all layers, `N` = first N layers). Works with Metal, CUDA, and ROCm wheels.
- `CHL_REVIEW_SHEET_ID` - Google Sheets ID for review
- `CHL_PUBLISHED_SHEET_ID` - Google Sheets ID for published entries

**Note:** The backend (cpu/metal/cuda/rocm) is automatically determined from `data/runtime_config.json` (created by `scripts/check_api_env.py`). No manual configuration needed.

For a complete list of configuration options, see [src/common/config/config.py](../src/common/config/config.py).

## 7. Troubleshooting

-   **Script won't run:** Ensure you are in the project root and using the `uv` environment (`uv run ...`).
-   **Import errors:** Your dependencies may be out of sync. Run `uv sync --python 3.11 --extra ml`.
-   **Permission denied:** Make scripts executable with `chmod +x scripts/<script_name>.py`.

## 8. Script Development Guidelines

When adding new scripts, follow the structure used in existing scripts like `scripts/search_health.py` or `scripts/validate_requirements.py`. Ensure they use `CHLAPIClient` for API communication and have clear documentation.

## 9. CPU-Only Mode

CHL can run in CPU-only mode without ML dependencies (FAISS, embeddings, reranker) using SQLite text search instead of semantic search.

### 9.1. When to Use CPU-Only Mode

Use CPU-only mode when:
- You don't have sufficient GPU VRAM (≥8 GB recommended for GPU mode)
- You don't need semantic search and keyword matching is sufficient
- You want to minimize dependencies and resource usage
- You're running on constrained hardware or in containers

### 9.2. Installation

Install CHL without ML extras:
```bash
uv sync --python 3.11
```

Run diagnostics to configure CPU mode:
```bash
python scripts/check_api_env.py
# Select option 1 for CPU-only mode
# This creates data/runtime_config.json with backend="cpu"
```

Run setup (no ML model downloads):
```bash
python scripts/setup-cpu.py
```

Start the server:
```bash
python -m uvicorn src.api.server:app --host 127.0.0.1 --port 8000
# Backend is automatically detected from data/runtime_config.json
```

### 9.3. Behavior Differences

In CPU-only mode:
- **Search**: Uses SQLite `LIKE` queries for keyword matching instead of semantic similarity
- **Duplicate detection**: Uses simple text matching instead of embedding-based similarity
- **Background worker**: No embedding worker runs; entries are immediately available for search
- **Vector components**: FAISS, embedding models, and reranker are not initialized
- **Imports**: `scripts/import.py` automatically skips worker coordination because `/admin/queue/*` endpoints are unavailable without the embedding worker

### 9.4. Search Tips for CPU-Only Mode

Since SQLite text search uses literal keyword matching:
- Use specific keywords from entry titles and content
- Search for exact phrases when possible
- Break complex queries into multiple searches
- Use category filtering to narrow results
- Avoid abstract or conceptual queries (e.g., "best practices" won't match "recommended approaches")

### 9.5. Switching Modes

**From CPU-only to GPU mode:**
1. Run diagnostics: `python scripts/check_api_env.py` and select GPU option
   - This updates `data/runtime_config.json` with the detected GPU backend (metal/cuda/rocm)
2. Install ML extras: `uv sync --python 3.11 --extra ml`
3. Download models: `uv run python scripts/setup-gpu.py --download-models`
4. Restart the API/MCP server (backend auto-detected from runtime_config.json)
5. Rebuild FAISS: Visit `/operations` and click **Rebuild Index**

**From GPU to CPU-only mode:**
1. Run diagnostics: `python scripts/check_api_env.py` and select option 1 (CPU-only)
   - This updates `data/runtime_config.json` with backend="cpu"
2. Restart the API/MCP server (backend auto-detected from runtime_config.json)
3. FAISS artifacts remain on disk but are ignored
4. Any pending embedding tasks are dropped on restart

**Important**: FAISS snapshots built in GPU mode are NOT compatible with CPU-only mode. When switching between modes, you must rebuild from scratch in the target mode.

### 9.6. Limitations

CPU-only mode has the following limitations:
- No semantic search or conceptual matching
- No embedding-based duplicate detection
- No reranking of search results
- Search quality depends on exact keyword matches
- Cannot import FAISS snapshots from GPU instances

For teams that need semantic search, consider running one GPU instance to build FAISS snapshots, but note that CPU-only instances cannot load these snapshots.
