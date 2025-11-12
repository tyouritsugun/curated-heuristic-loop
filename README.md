# CHL MCP Server

Curated Heuristic Loop (CHL) is a Model Context Protocol backend that helps code assistants remember what worked. Instead of forgetting between sessions, CHL keeps a shared memory of useful heuristics, searchable with FAISS and reranking, and lets teams curate everything through a browser UI.

For the full workflow philosophy see [doc/concept.md](doc/concept.md). For detailed operator procedures see [doc/manual.md](doc/manual.md).

## Quick Start

Choose your installation path based on your hardware and use case:

- **For GPU users** (≥8 GB VRAM, want semantic search): Follow the [GPU installation](#for-gpu-semantic-search) below to install ML dependencies and enable vector search with FAISS + embeddings.
- **For CPU-only users** (limited VRAM or keyword search is sufficient): Follow the [CPU-only installation](#for-cpu-only-keyword-search) to run CHL without ML dependencies using SQLite text search.

> **Note on switching modes**: FAISS snapshots built in GPU mode are NOT portable to CPU-only mode. Switching from CPU-only to GPU mode requires reinstalling ML extras, running `scripts/setup-gpu.py --download-models`, and rebuilding FAISS from scratch. See [Mode Switching](#mode-switching) for details.

### For GPU (Semantic Search)

1. **Install [uv](https://docs.astral.sh/uv/)** (one-line installer):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
2. **Clone the repo and enter it**:
   ```bash
   git clone https://github.com/tyouritsugun/curated-heuristic-loop.git
   cd curated-heuristic-loop
   ```
3. **Sync dependencies with ML extras** (includes FAISS + embedding clients):
   ```bash
   uv sync --python 3.11 --extra ml
   ```
   > The ML extra installs `faiss-cpu`, `sentence-transformers`, `llama-cpp-python`, etc., enabling vector search and reranking.

4. **Configure environment**:
   Apply the google service account and download the json credential file.
   Prepare the google spreadsheets for import and export, and share them with the account in your google service credential file with read and write permission.
   ```bash
   cp .env.sample .env
   # Edit .env and fill in:
   # - GOOGLE_CREDENTIAL_PATH (path to your service account JSON)
   # - IMPORT_SPREADSHEET_ID (published spreadsheet ID for imports)
   # - EXPORT_SPREADSHEET_ID (review spreadsheet ID for exports)
   ```

5. **Run first-time setup** (downloads models and initializes database):
   ```bash
   uv run python scripts/setup-gpu.py --download-models
   ```
   > This validates your environment, copies credentials, initializes the database, and downloads embedding/reranker models.

6. **Start the bundled FastAPI server**:
   ```bash
   uv run uvicorn src.api_server:app --host 127.0.0.1 --port 8000
   ```

7. **Open http://127.0.0.1:8000/settings** to verify configuration:
   - Configuration status shows your credential path and spreadsheet IDs (from `.env`)
   - Test connection to validate Google Sheets access
   - Review model selection and system diagnostics
   - Download JSON backup once everything looks good

8. **Open http://127.0.0.1:8000/operations** to run import:
   - Click **Run Import** to pull data from Google Sheets
   - Background worker automatically processes pending embeddings and updates FAISS index
   - No manual intervention needed - embeddings are generated within seconds of creating/updating entries

> ✅ That's it! All secrets live in `.env` file. Changes to credentials or sheet IDs take effect on next import/export (no restart needed).

### For CPU Only (Keyword Search)

If you don't have a GPU or don't need semantic search, you can run CHL in SQLite-only mode using keyword search:

1. **Install [uv](https://docs.astral.sh/uv/)** (one-line installer):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
2. **Clone the repo and enter it**:
   ```bash
   git clone https://github.com/tyouritsugun/curated-heuristic-loop.git
   cd curated-heuristic-loop
   ```
3. **Sync base dependencies** (without ML extras):
   ```bash
   uv sync --python 3.11
   ```
   > This installs only the base dependencies without embedding models, FAISS, or reranking components.

4. **Configure environment**:
   Apply the google service account and download the json credential file.
   Prepare the google spreadsheets for import and export, and share them with the account in your google service credential file with read and write permission.
   ```bash
   cp .env.sample .env
   # Edit .env and fill in:
   # - GOOGLE_CREDENTIAL_PATH (path to your service account JSON)
   # - IMPORT_SPREADSHEET_ID (published spreadsheet ID for imports)
   # - EXPORT_SPREADSHEET_ID (review spreadsheet ID for exports)
   # - CHL_SEARCH_MODE=sqlite_only  # Force SQLite-only mode
   ```

5. **Run first-time setup** (database initialization only):
   ```bash
   CHL_SEARCH_MODE=sqlite_only uv run python scripts/setup-cpu.py
   ```
   > This initializes the database and validates paths. No ML models are downloaded in CPU-only mode.

6. **Start the bundled FastAPI server**:
   ```bash
   CHL_SEARCH_MODE=sqlite_only uv run uvicorn src.api_server:app --host 127.0.0.1 --port 8000
   ```
   > The `CHL_SEARCH_MODE=sqlite_only` flag disables vector search components and uses SQLite text search exclusively.

7. **Open http://127.0.0.1:8000/settings** to verify configuration:
   - Test connection to validate Google Sheets access
   - Review system configuration

8. **Open http://127.0.0.1:8000/operations** to run import:
   - Click **Run Import** to pull data from Google Sheets
   - No background embedding worker runs in SQLite-only mode
   - Search uses keyword matching instead of semantic similarity

> ⚠️ **Important**: In SQLite-only mode, search uses literal keyword matching (LIKE queries) instead of semantic similarity. This works well for exact phrase searches but won't find conceptually related entries. For best results, use specific keywords from your entry titles and content.

### Mode Switching

**Switching from CPU-only (`sqlite_only`) to GPU (`auto`) mode:**
1. Set `CHL_SEARCH_MODE=auto` in `.env`
2. Install ML extras: `uv sync --python 3.11 --extra ml`
3. Download models: `uv run python scripts/setup-gpu.py --download-models`
4. Restart the API/MCP server
5. Rebuild embeddings/FAISS via `/operations` or `scripts/rebuild_index.py`

**Switching from GPU (`auto`) to CPU-only (`sqlite_only`) mode:**
1. Set `CHL_SEARCH_MODE=sqlite_only` in `.env`
2. Restart the API/MCP server
3. FAISS artifacts remain on disk but are ignored
4. Any pending embedding tasks are dropped

> **Important**: FAISS snapshots are NOT portable between modes. Switching modes requires rebuilding from scratch in the target mode.

## When you also need the CLI/MCP layers

The browser UI now covers all day-to-day administration. Use the CLI pieces only when you need automation or deep scripting:

- `uv run python scripts/seed_default_content.py` – idempotently loads starter categories and sample experiences.
- `uv run python scripts/export.py` – pushes local SQLite data to Google Sheets (uses `scripts/scripts_config.yaml`). Add `--dry-run` to preview counts.
- `uv run python scripts/import.py --yes` – pulls from Sheets (optionally coordinating with a worker pool if you deploy one). Once it finishes, upload or rebuild a FAISS snapshot via `/operations` so vector search reflects the curated data. Pass `--skip-api-coordination` only if the FastAPI server is offline.
- `uv run python scripts/setup-gpu.py [--download-models]` – optional helper that downloads models ahead of time; the web UI works without it, but this can save the first user from waiting.

### MCP clients (optional)

Add CHL to `~/.cursor/mcp.json` or another MCP-aware client if you want the assistant integration instead of (or alongside) the web UI:

```json
{
  "mcpServers": {
    "chl": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/curated_heuristic_loop", "run", "python", "src/server.py"]
    }
  }
}
```

> **Note:** The MCP server auto-loads configuration from `.env` file via python-dotenv. No `env` section needed in MCP client configuration unless you want to override specific values.

For Codex CLI (TOML format), add to `~/.config/codex/mcp.toml`:

```toml
[mcp_servers.chl]
command = "uv"
args = ["--directory", "/absolute/path/to/curated_heuristic_loop", "run", "python", "src/server.py"]

# Optional: Override .env values with explicit environment variables
# [mcp_servers.chl.env]
# CHL_EXPERIENCE_ROOT = "/custom/path/to/data"
# CHL_READ_DETAILS_LIMIT = "20"
```

Set `CHL_SKIP_MCP_AUTOSTART=1` in tests to prevent the auto HTTP bootstrap.

### Background workers & API endpoints

The same `uvicorn` process exposes REST APIs plus the `/settings` and `/operations` dashboards. By default it binds to `127.0.0.1`; if you proxy it anywhere else, provide your own authentication layer.

**Background Embedding Worker**: The API server automatically starts a background thread that polls for pending embeddings every 5 seconds and processes them incrementally. This means entries created or updated via MCP, API, or import are automatically embedded without manual intervention. The worker status is visible at http://127.0.0.1:8000/operations#ops-workers-card.

Configuration options:
- `CHL_WORKER_POLL_INTERVAL` - Seconds between polls (default: 5.0)
- `CHL_WORKER_BATCH_SIZE` - Max entries to process per batch (default: 10)
- `CHL_WORKER_AUTO_START` - Auto-start worker on server startup (default: 1, set to 0 to disable)

Need raw API access? Hit the documented routes under `/api/v1/` (settings, workers, operations, telemetry). Health checks live at `/health` and `/metrics` (Prometheus).

Import/export/index buttons call the same Python scripts you would run via the CLI. To keep them inert (for CI or local testing), set `CHL_OPERATIONS_MODE=noop` before starting the server. The default `scripts` mode executes the helpers with advisory locks and records stdout/stderr snippets in the job history.
Set `CHL_OPERATIONS_TIMEOUT_SEC` (default 900, minimum 60) to cap script runtime; jobs exceeding the limit are marked failed with tail logs captured. For faster MCP category/tool updates after settings changes, you can tune `CHL_CATEGORIES_CACHE_TTL` (seconds, default 30).

### System requirements

- Python 3.10 or 3.11 (uv installs/interprets 3.11 automatically on first run)
- Supported OS: macOS Apple Silicon, Linux x86_64/ARM64, Windows x86_64
- Recommended hardware: 16GB+ RAM (32GB if running local embeddings); GPU optional but helpful for FAISS rebuilds
- Google Service Account credential JSON + shared review sheet
- One MCP client at a time per repo clone (export/import scripts let you merge later)

## Web dashboards (Phases 0–3)

- **Settings** – First-time checklist, `scripts_config.yaml` loader (reads sheet IDs + credential/data paths), model picker, diagnostics, audit log, and JSON backup/restore. Everything is explained inline so non-technical operators can finish setup without digging into docs.
- **Operations** – Import/export/index triggers with last-run summaries, live queue depth, job history, and FAISS snapshot upload/download. Worker controls stay hidden unless you attach an external pool. Updates stream over SSE; fall back to manual refresh if SSE is blocked.

Both dashboards share the same process as the MCP/API stack, so every change is logged and subject to the same safety constraints (locks, validation, audit trail).

## Advanced references

- Workflow philosophy: [doc/concept.md](doc/concept.md)
- Operator runbooks & API details: [doc/manual.md](doc/manual.md)
- Web plan breakdown (Phases 0–3): [doc/plan/06_web_interface/](doc/plan/06_web_interface/)
- Advanced toggle: set `CHL_OPERATIONS_MODE=noop` before starting the server if you need the Operations buttons to stay in dry-run mode (the default `scripts` mode executes the CLI helpers).

## License

[MIT](LICENSE)
