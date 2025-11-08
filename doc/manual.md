# CHL Manual

This manual covers operational tasks, scripts, and workflows for the Curated Heuristic Loop system.
The main README now focuses on the one-command Quick Start; once the FastAPI server is running, the `/settings` page guides operators through setup in the browser.
Use this manual when you need deeper automation or want to script exports/imports.
For MCP client configuration (Cursor, Codex CLI), see README.md.

Preconditions
- Install [uv](https://docs.astral.sh/uv/) and ensure Python 3.10/3.11 is available (see README Quick Start).
- *(Optional)* Set experience root: `export CHL_EXPERIENCE_ROOT="$(pwd)/data"` (defaults to `<project_root>/data` if not set)

## Initial Setup

### First-Time Setup
```bash
cd /your/project/root
uv run python scripts/setup.py
```

> **Tip:** All script examples in this manual assume you're invoking them with the project’s managed environment. Prefix commands with `uv run` (e.g., `uv run python scripts/export.py`) if you rely on uv to resolve dependencies.

**Note:** `CHL_EXPERIENCE_ROOT` now defaults to `<project_root>/data` if not set. The data directory will be auto-created if it doesn't exist.

**Purpose:** One-time initialization for new installations

**When to use:**
- First time cloning the repository
- After deleting `data/` directory
- After changing embedding model selection (`CHL_EMBEDDING_REPO` / `CHL_EMBEDDING_QUANT`)
- To re-download corrupted models

**What it does:**
1. Creates `data/` directory structure
2. Initializes SQLite database (creates tables)
3. Downloads embedding & reranker models (if not already cached)
4. Creates FAISS index directory
5. Validates setup completeness

**Options:**
```bash
# Automatic setup (recommended) - uses smallest models (0.6B)
uv run python scripts/setup.py

# Interactive model selection - choose larger models (4B, 8B)
uv run python scripts/setup.py --download-models

# Force re-download models (if models are corrupted or you want to update)
uv run python scripts/setup.py --force-models
```

**Interactive Model Selection:**
When using `--download-models`, you'll see a menu to choose:
- **Embedding models**: 0.6B (~524 MB), 4B (~4 GB), 8B (~8 GB)
- **Reranker models**: 0.6B (~612 MB), 4B (~4 GB), 8B (~8 GB)

Larger models provide better quality but require more RAM/VRAM. For recommended model selection based on tested hardware, see the main [README](../README.md#system-requirements).

### Seed starter content (recommended)

Run this after setup to load the default CHL categories and sample entries and sync the guidelines **inside uv's environment**:

```bash
# Full seed: categories, sample entries, and guidelines (default)
uv run python scripts/seed_default_content.py

# Sync guidelines only (skip starter content)
uv run python scripts/seed_default_content.py --skip-seed

# Seed content only (skip guideline sync)
uv run python scripts/seed_default_content.py --skip-guidelines
```

This command is idempotent; rerun it to restore starter data or refresh guidelines.

The guideline sync keeps the `GLN` category aligned with the Markdown sources. It re-imports `generator.md` and `evaluator.md`, deletes stale manuals, and removes a guide if its source file is missing. After syncing, you can retrieve the manuals via MCP:

```bash
codex-cli mcp tool call chl get_guidelines --params guide_type=generator
codex-cli mcp tool call chl get_guidelines --params guide_type=evaluator
```

**Output (first run):**
```
============================================================
  CHL MCP Server - First-Time Setup
============================================================

✓ Data directory: data/
✓ FAISS index directory: data/faiss_index/
✓ Database initialized: data/chl.db
  - 0 categories
  - 0 experiences
  - 0 manuals

Downloading models (this may take 5-10 minutes)...

  [1/2] Qwen/Qwen3-Embedding-0.6B
        ✓ Downloaded (dimension: 1024)

  [2/2] Qwen/Qwen3-Reranker-0.6B
        ✓ Downloaded

✓ Models ready
✓ Setup validation passed
```

**Output (subsequent runs - models cached):**
```
✓ Models already cached
  - Embedding: Qwen/Qwen3-Embedding-0.6B
  - Reranker: Qwen/Qwen3-Reranker-0.6B
  (Use --force-models to re-download)

============================================================
  Setup Complete!
============================================================

Next steps:

  1. Start the FastAPI server:
     uv run uvicorn src.api_server:app --host 127.0.0.1 --port 8000

  2. Finish onboarding at http://127.0.0.1:8000/settings, then rebuild/upload a FAISS snapshot via /operations before enabling vector search.
```

**Preconditions:**
- Python 3.10 or 3.11 (install once with `uv python install 3.11`)
- Install dependencies with the supported runtime (includes llama-cpp, FAISS, gspread): `uv sync --python 3.11 --extra ml`
- `CHL_EXPERIENCE_ROOT` environment variable set (defaults to `<project_root>/data` if omitted)
- Internet connection (for model download)

**Troubleshooting:**
- "ML dependencies not found": Install ML extras with `uv sync --python 3.11 --extra ml`
- "Model download failed": Check internet connection, try `python scripts/setup.py --force-models`
- "Permission denied": Use `chmod +x scripts/setup.py`
- Models already downloaded but showing as missing: Try `python scripts/setup.py --force-models`

---

## 1: Search & Embeddings

### Rebuild Search Index
```bash
python scripts/rebuild_index.py
```

**Purpose:** Regenerate embeddings and FAISS index from scratch.

**When to use:**
- After changing embedding model selection (`CHL_EMBEDDING_REPO` / `CHL_EMBEDDING_QUANT`)
- After bulk data imports
- When FAISS index becomes corrupted
- When embedding_status shows many 'failed' entries

**Process:**
1. Deletes existing embeddings and FAISS metadata
2. Generates embeddings for all experiences and manuals
3. Builds new FAISS index
4. Updates embedding_status to 'embedded'

**Environment variables (optional overrides):**
- `CHL_EXPERIENCE_ROOT` - path to data directory
- `CHL_DATABASE_PATH` - path to SQLite database
- `CHL_EMBEDDING_REPO` / `CHL_EMBEDDING_QUANT` - override the embedding GGUF selection (defaults recorded by `scripts/setup.py`)
- `CHL_RERANKER_REPO` / `CHL_RERANKER_QUANT` - override reranker selection (defaults recorded by `scripts/setup.py`)

**Output:**
- Progress bar showing embedding generation
- Final statistics: entities processed, time taken, errors
- New FAISS index files in `data/faiss_index/`

---

### Manage FAISS Snapshots (Web UI preferred)
1. Start the FastAPI server: `uv run uvicorn src.api_server:app --host 127.0.0.1 --port 8000`
2. Open `http://127.0.0.1:8000/operations`.
3. Use the **FAISS Snapshot** card to:
   - **Download** the current `.index/.meta/.backup` files as a ZIP for safekeeping or to hand off to another machine.
   - **Upload** a new snapshot (ZIP, ≤512 MiB). The server validates the archive, writes it under `CHL_FAISS_INDEX_PATH`, logs the action, and hot-reloads the vectors when possible.
4. Queue telemetry + audit log entries confirm who performed each snapshot swap.

Security and limits:
- Allowed file extensions in the snapshot: `.index`, `.json`, `.backup` (case-insensitive)
- Per-file size limit: 512 MiB (uploads larger than this are rejected)
- Archive size limit: 512 MiB
- The server validates all ZIP entries before extraction and performs secure, file-by-file writes.

> Tip: run `uv run python scripts/rebuild_index.py` on a machine with the ML extras when you need to regenerate embeddings, then upload the resulting snapshot through the Operations dashboard.

---

### Search Health Check
```bash
python scripts/search_health.py
```

**Purpose:** Inspect search index health and statistics.

**When to use:**
- Diagnosing search performance issues
- Monitoring embedding status
- Validating index integrity

**Output:**
```json
{
  "totals": {"experiences": 150, "manuals": 25},
  "embedding_status": {"pending": 5, "embedded": 168, "failed": 2},
  "faiss": {
    "available": true,
    "model": "Qwen/Qwen3-Embedding-0.6B",
    "dimension": 1024,
    "vectors": 168,
    "by_type": {"experience": 145, "manual": 23},
    "index_path": "data/faiss_index/unified_qwen_qwen3-embedding-06b.index",
    "last_updated": "2025-10-26T15:30:00Z"
  },
  "warnings": [
    "2 entities have failed embeddings",
    "5 entities have pending embeddings"
  ]
}
```

---

## 2: Export & Import

The export/import scripts read their settings from `scripts/scripts_config.yaml`.
Populate that file once and re-run the scripts with a single command. A minimal
example:

```yaml
# scripts/scripts_config.yaml
data_path: ../data
google_credentials_path: ../credentials/service_account.json

export:
  # sheet_id: your-review-sheet-id
  experiences_sheet:
    # id: your-review-sheet-id
    worksheet: Experiences
  manuals_sheet:
    # id: your-review-sheet-id
    worksheet: Manuals

import:
  # sheet_id: your-published-sheet-id
  experiences_sheet:
    # id: your-published-sheet-id
    worksheet: Experiences
  manuals_sheet:
    # id: your-published-sheet-id
    worksheet: Manuals
```

Uncomment and replace the `sheet_id`/`id` placeholders with your actual Google
Sheet identifiers. Adjust paths as needed; relative values are resolved relative
to the config file.

### Check Export Status (optional)
```bash
```
Use this to review pending entries or sync metadata before exporting.

### Export to Google Sheets
```bash
uv run python scripts/export.py
```
- Writes **all** experiences and manuals from SQLite to the configured
  worksheets.
- Add `--dry-run` to preview counts without touching Google Sheets.

### Import from Google Sheets (destructive)
```bash
uv run python scripts/import.py --yes
```
- Replaces the local experiences/manual tables with the worksheet contents.
- After import, upload or rebuild a FAISS snapshot via `/operations` (or run `uv run python scripts/rebuild_index.py` on a machine with ML extras) so vector search reflects the curated sheet.
- Omit `--yes` to receive an interactive confirmation prompt.

---

## Complete Workflow Summary (MVP)

This section describes the end-to-end workflow for team curation using Google Sheets.

### Setup (One-Time)

1. **Create Google Sheets**
   - Review Sheet: For curator review of pending entries
   - Published Sheet: Curated, approved entries for team sync

2. **Set up Service Account**
   - Create service account in Google Cloud Console
   - Download credentials JSON
   - Share both sheets with service account email
   - (Optional) Set `CHL_GOOGLE_CREDENTIALS_PATH` environment variable if you prefer managing credentials outside `scripts/scripts_config.yaml`

3. **Configure script settings**
   - Edit `scripts/scripts_config.yaml` and populate the `export` and `import`
     sections with your Google Sheet IDs, worksheet names, credentials path,
     and any custom data directory overrides.

### Regular Workflow

#### 1. Export to Review Sheet
```bash
uv run python scripts/export.py
```
Write the full SQLite dataset—categories, experiences, and manuals—to the configured Google Sheets worksheets.
You can also create `scripts/scripts_config.yaml` to store defaults (credentials, sheet IDs, data path, etc.) and simply run `uv run python scripts/export.py`; CLI flags still override the YAML values.

#### 2. Manual Curation (Google Sheets)
Curators work directly in the exported worksheets:
- Edit `title`, `playbook`, `content`, or `summary` fields to capture the
  curated guidance.
- Adjust `section` when recategorising experiences; ensure values remain one of
  `useful`, `harmful`, or `contextual`.
- Remove rows that should not be published or add new ones inline if needed.
- Leave the `id` and `category_code` columns unchanged so imports remain stable.

Sheet columns (exported as plain values):
- Categories: `code`, `name`, `description`, `created_at`.
- Experiences: `id`, `category_code`, `section`, `title`, `playbook`, `context`,
  `source`, `sync_status`, `author`, `embedding_status`, `created_at`,
  `updated_at`, `synced_at`, `exported_at`.
- Manuals: `id`, `category_code`, `title`, `content`, `summary`, `source`,
  `sync_status`, `author`, `embedding_status`, `created_at`, `updated_at`,
  `synced_at`, `exported_at`.

Keep the identifier and category columns stable—downstream imports expect them
unchanged. Provide ISO 8601 strings when editing timestamp columns.

### Workflow Checklist

1. **Export** – `uv run python scripts/export.py` writes the latest SQLite entries
   to Google Sheets using the configuration in `scripts/scripts_config.yaml`.
2. **Curate in Sheets** – reviewers edit the exported category/experience/manual worksheets, remove rows, or adjust values as needed. Ensure IDs stay unique and category codes remain three-letter uppercase values.
3. **Import** – `uv run python scripts/import.py --yes` overwrites the local
   categories, experiences, and manuals with the curated sheet content. Follow up by uploading/rebuilding a FAISS snapshot via `/operations` (or `uv run python scripts/rebuild_index.py`) so vector search reflects the curated data.
4. **Regenerate embeddings** – When you need a fresh snapshot (after large imports or model upgrades), run `uv run python scripts/rebuild_index.py` on a machine with the ML extras, then upload the resulting ZIP via the Operations dashboard. Restart MCP clients if they were pointing at outdated vectors.

Tips:
- Keep a backup of `data/chl.db` if you want the option to roll back imports.
- The import script treats empty strings as NULL/None. Provide ISO 8601 strings
  for timestamp fields when editing directly in Sheets.

---

---

## 3: API Server Operations

The optional FastAPI server provides REST endpoints and asynchronous embedding processing. The MCP server and API server are independent—use the API for high-volume workflows or programmatic integration.

### Start the API Server
```bash
uv run uvicorn src.api_server:app --host 0.0.0.0 --port 8000
```

**Output** (successful startup):
```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
{"timestamp": "2025-11-06T...", "level": "INFO", "message": "Starting CHL API server..."}
{"timestamp": "2025-11-06T...", "level": "INFO", "message": "Database initialized: /path/to/chl.db"}
{"timestamp": "2025-11-06T...", "level": "INFO", "message": "Search service initialized with primary provider: vector_faiss"}
{"timestamp": "2025-11-06T...", "level": "INFO", "message": "Worker pool initialized with 2 workers"}
{"timestamp": "2025-11-06T...", "level": "INFO", "message": "Embedding worker pool started"}
{"timestamp": "2025-11-06T...", "level": "INFO", "message": "CHL API server started successfully"}
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### Health Check
```bash
curl http://localhost:8000/health
```

Returns service status and component health:
```json
{
  "status": "healthy",
  "components": {
    "database": {"status": "available", "path": "/path/to/chl.db"},
    "faiss_index": {"status": "available", "vectors": 150},
    "embedding_model": {"status": "available", "model": "Qwen/Qwen3-Embedding-0.6B"}
  },
  "timestamp": "2025-11-06T..."
}
```

### Monitor Embedding Queue
```bash
curl http://localhost:8000/admin/queue/status
```

Returns queue depth and worker status:
```json
{
  "queue": {
    "pending": {"experiences": 5, "manuals": 2, "total": 7},
    "failed": {"experiences": 0, "manuals": 0, "total": 0}
  },
  "workers": {
    "num_workers": 2,
    "workers": [
      {
        "worker_id": 0,
        "running": true,
        "paused": false,
        "jobs_processed": 150,
        "jobs_succeeded": 148,
        "jobs_failed": 2,
        "last_run": 1699520400.0
      },
      {
        "worker_id": 1,
        "running": true,
        "paused": false,
        "jobs_processed": 143,
        "jobs_succeeded": 142,
        "jobs_failed": 1,
        "last_run": 1699520398.0
      }
    ],
    "total_jobs_processed": 293,
    "total_jobs_succeeded": 290,
    "total_jobs_failed": 3
  }
}
```

### Pause/Resume Workers
```bash
# Pause all workers (for maintenance)
curl -X POST http://localhost:8000/admin/queue/pause

# Resume workers
curl -X POST http://localhost:8000/admin/queue/resume
```

**When to use**:
- Before manual database operations
- During bulk imports (automatic when using `scripts/import.py`)
- For controlled maintenance windows

### Retry Failed Embeddings
```bash
curl -X POST http://localhost:8000/admin/queue/retry-failed
```

Resets all failed entries to `pending` status so workers retry them:
```json
{
  "retried": {
    "experiences": 2,
    "manuals": 1,
    "total": 3
  }
}
```

### Drain Queue (Wait for Completion)
```bash
curl -X POST "http://localhost:8000/admin/queue/drain?timeout=300"
```

Blocks until all pending jobs are processed (max 5 minutes):
```json
{
  "status": "drained",
  "elapsed": 45.2
}
```

**When to use**:
- Before shutting down the API server
- Before bulk import operations (automatic)
- To ensure all embeddings are complete before backups

### FAISS Index Status
```bash
curl http://localhost:8000/admin/index/status
```

Returns index health and configuration:
```json
{
  "status": "available",
  "index_size": 150,
  "tombstone_ratio": 0.02,
  "needs_rebuild": false,
  "save_policy": "periodic",
  "rebuild_threshold": 0.2,
  "model_name": "Qwen/Qwen3-Embedding-0.6B",
  "dimension": 1024
}
```

### Trigger Index Operations
```bash
# Force save FAISS index to disk
curl -X POST http://localhost:8000/admin/index/save

# Force rebuild FAISS index from embeddings
curl -X POST http://localhost:8000/admin/index/rebuild
```

**Warning**: Rebuild is a blocking operation that may take several seconds.

### Web UI (Settings & Operations)

When the FastAPI server is running you can manage CHL entirely from the browser on the same machine. The topmost card on `/settings` is a first-time checklist that walks new operators through credential placement, sheet IDs, model selection, diagnostics, and jumping over to `/operations`—no README digging required.

- **Settings Dashboard** – `http://127.0.0.1:8000/settings`
  - Credentials: upload the Google service-account JSON (copied into `<experience_root>/credentials` with `0600` perms) *or* point to an existing local path. SQLite stores only the path/checksum/validated timestamp; the JSON bytes never enter the database.
  - Sheets: configure the Review/Published sheet IDs and tab names without touching `scripts/scripts_config.yaml`. The UI writes these values to SQLite for API/UI flows; keep the YAML file in sync manually until the export/import scripts migrate.
  - Models: choose embedding/reranker repos + quantization; changes update `data/model_selection.json` so workers pick them up.
  - Diagnostics & backups: validate credentials/Sheets connectivity, view the latest audit events, and download/restore a JSON snapshot of non-secret metadata.

- **Operations Dashboard** – `http://127.0.0.1:8000/operations`
  - Import/export/index buttons call the same CLI helpers (`scripts/import.py`, `scripts/export.py`, `scripts/rebuild_index.py`) with advisory locks and surface stdout/stderr snippets inline; results trigger an `ops-refresh` event so queue/worker/job cards update immediately.
  - Worker card only exposes pause/resume/drain when an external worker pool registers; otherwise it stays informational (“no workers connected”) so manual FAISS snapshots are the default workflow.
  - Queue and job cards stream over SSE/htmx every five seconds; if SSE drops, the `ops-refresh` events keep things in sync.
  - **FAISS snapshots**: download the current `.index/.meta/.backup` files as a ZIP or upload a ZIP built from the same files. Uploads are limited to 512 MiB, require ZIP format, write files directly to `CHL_FAISS_INDEX_PATH`, log an `index.snapshot.uploaded` audit entry, and attempt to hot-reload the in-memory FAISS manager. If the reload fails, restart the API server to pick up the files.

The UI binds to `127.0.0.1` by default; place your own authenticated reverse proxy in front if you must expose it elsewhere.

### Configuration

Environment variables for API server:

| Variable | Default | Purpose |
|----------|---------|---------|
| `CHL_NUM_EMBEDDING_WORKERS` | `2` | *Legacy.* Count for the removed background worker pool (ignored in current builds). |
| `CHL_WORKER_POLL_INTERVAL` | `5` | *Legacy.* Poll interval for the archived worker pool. |
| `CHL_WORKER_BATCH_SIZE` | `10` | *Legacy.* Batch size for the archived worker pool. |
| `CHL_FAISS_SAVE_POLICY` | `periodic` | When to save index (`manual`, `periodic`, `immediate`) |
| `CHL_FAISS_SAVE_INTERVAL` | `300` | Seconds between periodic saves |
| `CHL_FAISS_REBUILD_THRESHOLD` | `0.2` | Tombstone ratio triggering rebuild (0.0-1.0) |
| `CHL_OPERATIONS_MODE` | `scripts` | Controls `/operations` job handlers. Use `scripts` (default) to execute the CLI helpers, or `noop` to keep the buttons inert (handy for CI/tests). |

### Background Worker Lifecycle

> **Legacy notice:** Local embedding workers are no longer bundled. This section remains for operators wiring up an external pool via the API.

1. **Startup**: Workers are created when API server starts (if ML dependencies available)
2. **Polling**: Each worker polls for pending entries every `WORKER_POLL_INTERVAL` seconds
3. **Batch Processing**: Workers fetch up to `WORKER_BATCH_SIZE` entries and generate embeddings
4. **Status Updates**: Entries are marked as `embedded` or `failed` after processing
5. **FAISS Updates**: Index is updated incrementally as embeddings complete
6. **Shutdown**: Workers drain pending jobs (up to 30s) before server shutdown

### Troubleshooting

**Workers not starting**:
- Check logs: `{"message": "Worker pool not initialized (embedding service unavailable)"}`
- Install ML dependencies: `uv sync --python 3.11 --extra ml`
- Verify models are downloaded: `uv run python scripts/setup.py`

**High failure rate**:
- Check worker logs for specific errors
- Retry failed jobs: `curl -X POST http://localhost:8000/admin/queue/retry-failed`
- If errors persist, rebuild index: `python scripts/rebuild_index.py`

**Queue not draining**:
- Check worker status: `curl http://localhost:8000/admin/queue/status`
- Ensure workers are not paused
- Check for stuck entries (status still `pending` after long time)

---

## Script Development Guidelines

When adding new scripts:

1. **Location:** Place in `scripts/` directory
2. **Naming:** Use snake_case, descriptive names
3. **Structure:**
   ```python
   #!/usr/bin/env python3
   """One-line description of what this script does."""
   import sys
   from pathlib import Path

   # Add project root to path
   sys.path.insert(0, str(Path(__file__).parent.parent))

   from src.config import get_config
   # ... other imports

   def main():
       # Script logic here
       pass

   if __name__ == "__main__":
       main()
   ```

4. **Documentation:** Add entry to this file with:
   - Command to run
   - Purpose
   - When to use
   - Expected output

5. **Error Handling:**
   - Print clear error messages
   - Exit with non-zero code on failure
   - Log to console (not just files)

6. **Environment:**
   - Use `get_config()` for configuration
   - Respect environment variables
   - Provide sensible defaults

---

## Troubleshooting

### Script won't run
```bash
# Make sure you're in project root
cd /path/to/curated-heuristic-loop

# Check Python path
python --version  # Should be 3.10+ (uv can pin 3.11 via `uv python install 3.11`)

# Run with uv-managed environment
```

### Import errors
```bash
# Sync dependencies (includes ML extras)
uv sync --python 3.11 --extra ml
```

### Permission denied
```bash
# Make script executable (if needed)
chmod +x scripts/rebuild_index.py
```

---

## Environment Variable Reference

Scripts respect these environment variables (legacy support).
When using `scripts/export.py` and `scripts/import.py`, prefer configuring
`scripts/scripts_config.yaml` instead of environment variables.

### Core Settings
| Variable | Default | Purpose |
|----------|---------|---------|
| `CHL_EXPERIENCE_ROOT` | `<project_root>/data` | Path to data directory |
| `CHL_DATABASE_PATH` | `<experience_root>/chl.db` | SQLite database path (relative values resolve under `<experience_root>`) |
| `CHL_DATABASE_ECHO` | `false` | Log SQL queries |

### Search & ML
| Variable | Default | Purpose |
|----------|---------|---------|
| `CHL_EMBEDDING_REPO` | Recorded in `data/model_selection.json` | Embedding GGUF repository (e.g., `Qwen/Qwen3-Embedding-0.6B-GGUF`) |
| `CHL_EMBEDDING_QUANT` | Recorded in `data/model_selection.json` | Embedding quantization (e.g., `Q8_0`) |
| `CHL_RERANKER_REPO` | Recorded in `data/model_selection.json` | Reranker GGUF repository |
| `CHL_RERANKER_QUANT` | Recorded in `data/model_selection.json` | Reranker quantization |
| `CHL_FAISS_INDEX_PATH` | `<experience_root>/faiss_index` | FAISS index directory (relative values resolve under `<experience_root>`) |

### Export & Sync
| Variable | Default | Purpose |
|----------|---------|---------|
| `CHL_REVIEW_SHEET_ID` | (none) | Google Sheets ID for Review Sheet |
| `CHL_PUBLISHED_SHEET_ID` | (none) | Google Sheets ID for Published Sheet |
| `CHL_EXPORT_BATCH_SIZE` | `1000` | Rows per API call |

These variables remain available for legacy tooling, but the preferred
configuration path is `scripts/scripts_config.yaml` for export/import scripts.

### Operations & Caching
| Variable | Default | Purpose |
|----------|---------|---------|
| `CHL_OPERATIONS_MODE` | `scripts` | Use built-in scripts or `noop` for dry-run |
| `CHL_OPERATIONS_TIMEOUT_SEC` | `900` (min `60`) | Max seconds per import/export/index operation |
| `CHL_CATEGORIES_CACHE_TTL` | `30.0` | MCP categories/tool index cache TTL (seconds) |
