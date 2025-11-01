# CHL MCP Server

Curated Heuristic Loop (CHL) MCP Server – the Model Context Protocol backend for managing experience-based knowledge. For the full system overview and workflow philosophy, see [doc/chl_guide.md](doc/chl_guide.md).

## Setup

### Prerequisites

- [uv](https://docs.astral.sh/uv/) - Fast Python package installer
- Python 3.10 or 3.11
- **Platform Requirements:**
  - macOS: Apple Silicon (ARM/M1/M2/M3) required
  - Intel Mac (x86_64) is **not supported** due to PyTorch compatibility
  - Linux: x86_64 or ARM64
  - Windows: x86_64
- Google Service Account credentials (for export/sync to Google Sheets)
- Google Sheet for logging

### Installation

1. **Install uv (if not already installed):**
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Clone and setup the project:**
   ```bash
   git clone <repository-url>
   cd curated_heuristic_loop
   ```

3. **Install the supported Python runtime (once):**
   ```bash
   uv python install 3.11
   ```
   > Torch/Faiss wheels are only published for Python 3.10–3.11. Using 3.12 triggers build failures.

4. **Install dependencies with the 3.11 interpreter:**
   ```bash
   uv sync --python 3.11 --extra ml
   ```
   This creates/updates uv’s managed environment and installs the full ML stack (`sentence-transformers`, PyTorch, FAISS, llama-cpp`). You do not need to create or activate a separate `.venv`—uv will reuse this environment automatically.

5. **Run first-time setup:**
   ```bash
   # Automatic setup - uses smallest models (0.6B)
   uv run python scripts/setup.py

   # Interactive model selection - choose larger models (4B, 8B)
   uv run python scripts/setup.py --download-models

   ```

   This will:
   - Create database and data directory structure
   - Auto-install ML dependencies if missing (`uv sync --python 3.11 --extra ml`)
   - Download embedding models (~1.1 GB, if ML dependencies installed)
   - Validate setup completeness
   - For details about the setup, see [manual](./doc/chl_manual.md#2-search--embeddings)

6. **Seed starter content + sync guidelines:**
   ```bash
   uv run python scripts/seed_default_content.py
   ```
   This single command:
   - Inserts the default CHL categories and sample entries (idempotent)
   - Syncs the `GLN` guidelines category from `generator.md` and `evaluator.md`
   Rerun any time to restore starter content or refresh guidelines.

7. **Configure MCP settings:**
   
   Add to your `~/.cursor/mcp.json`, or to an MCP client that accepts JSON. `CHL_EXPERIENCE_ROOT` is optional; if omitted, CHL uses `<project_root>/data` and auto-creates it on first run. `CHL_DATABASE_PATH` and `CHL_FAISS_INDEX_PATH` default to `<experience_root>/chl.db` and `<experience_root>/faiss_index` (relative values are resolved under `<experience_root>`). `CHL_GOOGLE_CREDENTIALS_PATH` is optional—export/import scripts already read the path from `scripts/scripts_config.yaml`, so set the env var only if you prefer managing it outside the YAML. `CHL_READ_DETAILS_LIMIT` defaults to `10`, so include it only if you need a different value.
   
   ```json
   {
     "mcpServers": {
       "chl": {
        "command": "uv",
        "args": ["--directory", "/absolute/path/to/curated_heuristic_loop", "run", "python", "src/server.py"],
        "env": {
          // Optional env overrides (use scripts/scripts_config.yaml for defaults)
          // "CHL_GOOGLE_CREDENTIALS_PATH": "/absolute/path/to/credentials/service_account.json",
          // "CHL_EXPERIENCE_ROOT": "/absolute/path/to/curated_heuristic_loop/data",
          // Optional overrides (defaults shown)
          // "CHL_DATABASE_PATH": "/absolute/path/to/curated_heuristic_loop/data/chl.db",
          // "CHL_FAISS_INDEX_PATH": "/absolute/path/to/curated_heuristic_loop/data/faiss_index",
          // "CHL_READ_DETAILS_LIMIT": "10",
          // Export pipeline (configure after preparing Google Sheets):
          // "CHL_REVIEW_SHEET_ID": "your-review-sheet-id",
          // "CHL_PUBLISHED_SHEET_ID": "your-published-sheet-id"
        }
      }
    }
  }
   ```
   
   For Codex CLI (TOML format), add to `~/.config/codex/mcp.toml`:
   
   ```toml
   [mcp_servers.chl]
   command = "uv"
   args = ["--directory", "/absolute/path/to/curated_heuristic_loop", "run", "python", "src/server.py"]

   [mcp_servers.chl.env]
   # Optional override if you prefer env vars for the Google credentials path
   # CHL_GOOGLE_CREDENTIALS_PATH = "/absolute/path/to/credentials/service_account.json"
   # CHL_EXPERIENCE_ROOT = "/absolute/path/to/curated_heuristic_loop/data"
   # Optional overrides (defaults shown)
   # CHL_DATABASE_PATH = "/absolute/path/to/curated_heuristic_loop/data/chl.db"
   # CHL_FAISS_INDEX_PATH = "/absolute/path/to/curated_heuristic_loop/data/faiss_index"
   # Export pipeline (configure after preparing Google Sheets)
   # CHL_REVIEW_SHEET_ID = "your-review-sheet-id"
   # CHL_PUBLISHED_SHEET_ID = "your-published-sheet-id"
   ```

   **Note:** The `--directory` flag tells `uv` where to find the `pyproject.toml` file for dependency management.
   **Note:** After this step it should be possible to ask your code assistant "Can you access your CHL toolset? ", if can not, then you can go to `data/log` to see the log and troubleshot the problem.

8. **Restart Cursor or other code assistant** to load the MCP server configuration.

9. **Verify MCP integration (optional but recommended):**
   - `list_categories` (or `codex-cli mcp describe chl`) should list the seeded CHL shelves.
   - Fetch the guidelines via MCP:
     ```bash
     codex-cli mcp tool call chl get_guidelines --params guide_type=generator
     ```
     (Use `guide_type=evaluator` for the evaluator version.)

## Import & Export

### Prepare Google Sheets

- Create the Google Sheet(s) you plan to sync with. You can use a single sheet or split review/published sheets depending on your workflow.
- Create a service account in Google Cloud Console.
- Download the credentials JSON file.
- Share each sheet with the service account email.
- See the [manual](./doc/chl_manual.md#3-export--import-mvp) for the end-to-end workflow and role expectations.

### Configure script defaults

1. Edit `scripts/scripts_config.yaml` and fill in the commented placeholders:
   - Set `data_path` and `google_credentials_path` if you want paths different from the defaults.
   - Under both `export` and `import`, provide the `sheet_id` (or per-sheet `id`) and confirm the worksheet names.
   - If you use separate review/published sheets, use different IDs in those sections. Shared IDs also work when you curate in a single sheet.
2. Optionally override settings via CLI flags when running the scripts—the YAML provides the base defaults.

### Run the sync scripts

- `uv run python scripts/export.py` – writes the local SQLite content to the configured worksheets. Add `--dry-run` to preview counts without making changes.
- `uv run python scripts/import.py --yes` – replaces local tables with the sheet contents and automatically regenerates embeddings/FAISS metadata (skip with `--skip-embeddings`). If you skip or the sync is skipped due to missing ML dependencies, run `python scripts/sync_embeddings.py --retry-failed` afterwards and restart the MCP server so it reloads the updated index.


## License

See project [license](LICENSE) file.
