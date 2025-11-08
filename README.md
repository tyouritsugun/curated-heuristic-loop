# CHL MCP Server

Curated Heuristic Loop (CHL) is a Model Context Protocol backend that helps code assistants remember what worked. Instead of forgetting between sessions, CHL keeps a shared memory of useful heuristics, searchable with FAISS and reranking, and lets teams curate everything through a browser UI.

For the full workflow philosophy see [doc/chl_guide.md](doc/chl_guide.md). For detailed operator procedures see [doc/chl_manual.md](doc/chl_manual.md).

## Quick Start (Web UI first)

1. **Install [uv](https://docs.astral.sh/uv/)** (one-line installer):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
2. **Clone the repo and enter it**:
   ```bash
   git clone https://github.com/<your-org>/curated-heuristic-loop.git
   cd curated-heuristic-loop
   ```
3. **Start the bundled FastAPI server** (first run downloads every dependency automatically):
   ```bash
   uv run uvicorn src.api_server:app --host 127.0.0.1 --port 8000
   ```
4. **Open http://127.0.0.1:8000/settings**. The page walks you through:
   - Uploading (or pointing at) your Google service-account JSON. Secrets stay on disk under `<experience_root>/credentials` with `0600` perms; SQLite only stores path/checksum/validation time.
   - Entering the Spreadsheet ID + tab names for the review/published sheets. If you already have `scripts/scripts_config.yaml`, copy the IDs from there (UI never edits that file).
   - Picking embedding/reranker models and running the built-in diagnostics check.
   - Downloading a JSON backup once things look good.

As soon as the Settings checklist is green, click **Operations** in the nav bar or visit `http://127.0.0.1:8000/operations` to trigger imports/exports/index rebuilds, pause or drain workers, and monitor queue/job telemetry via SSE without touching the CLI.

> ✅ That’s it for most users: run one command to start the server, then use the in-app instructions. No Python, MCP, or database knowledge required.

## When you also need the CLI/MCP layers

The browser UI now covers all day-to-day administration. Use the CLI pieces only when you need automation or deep scripting:

- `uv run python scripts/seed_default_content.py` – idempotently loads starter categories and sample experiences.
- `uv run python scripts/export.py` – pushes local SQLite data to Google Sheets (uses `scripts/scripts_config.yaml`). Add `--dry-run` to preview counts.
- `uv run python scripts/import.py --yes` – pulls from Sheets (optionally coordinating with a worker pool if you deploy one). Once it finishes, upload or rebuild a FAISS snapshot via `/operations` so vector search reflects the curated data. Pass `--skip-api-coordination` only if the FastAPI server is offline.
- `uv run python scripts/setup.py [--download-models]` – optional helper that downloads models ahead of time; the web UI works without it, but this can save the first user from waiting.

### MCP clients (optional)

Add CHL to `~/.cursor/mcp.json` or another MCP-aware client if you want the assistant integration instead of (or alongside) the web UI:

```json
{
  "mcpServers": {
    "chl": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/curated_heuristic_loop", "run", "python", "src/server.py"],
      "env": {
        // Optional overrides; see doc/chl_manual.md for all keys
        // "CHL_GOOGLE_CREDENTIALS_PATH": "/path/to/credentials.json",
        // "CHL_EXPERIENCE_ROOT": "/path/to/data"
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
# Optional overrides; prefer scripts/scripts_config.yaml for defaults
# CHL_GOOGLE_CREDENTIALS_PATH = "/absolute/path/to/credentials/service_account.json"
# CHL_EXPERIENCE_ROOT = "/absolute/path/to/curated_heuristic_loop/data"
# CHL_DATABASE_PATH = "/absolute/path/to/curated_heuristic_loop/data/chl.db"
# CHL_FAISS_INDEX_PATH = "/absolute/path/to/curated_heuristic_loop/data/faiss_index"
# CHL_READ_DETAILS_LIMIT = "10"
# Export pipeline (configure after preparing Google Sheets)
# CHL_REVIEW_SHEET_ID = "your-review-sheet-id"
# CHL_PUBLISHED_SHEET_ID = "your-published-sheet-id"
```

`CHL_MCP_HTTP_MODE` controls whether MCP tools talk to the HTTP API (`http`), fall back to direct handlers (`auto`, the default), or stay fully local (`direct`). Use `--chl-http-mode` on the CLI to override per run. Set `CHL_SKIP_MCP_AUTOSTART=1` in tests to prevent the auto HTTP bootstrap.

### Background workers & API endpoints

The same `uvicorn` process exposes REST APIs plus the `/settings` and `/operations` dashboards. By default it binds to `127.0.0.1`; if you proxy it anywhere else, provide your own authentication layer.

Need raw API access? Hit the documented routes under `/api/v1/` (settings, workers, operations, telemetry). Health checks live at `/health` and `/metrics` (Prometheus). Worker controls (`/ui/workers/*`) remain for deployments that wire up an external embedding pool; by default they return `503 Worker pool not initialized`, so the UI nudges you toward the manual FAISS snapshot workflow.

Import/export/index buttons call the same Python scripts you would run via the CLI. To keep them inert (for CI or local testing), set `CHL_OPERATIONS_MODE=noop` before starting the server. The default `scripts` mode executes the helpers with advisory locks and records stdout/stderr snippets in the job history.
Set `CHL_OPERATIONS_TIMEOUT_SEC` (default 900, minimum 60) to cap script runtime; jobs exceeding the limit are marked failed with tail logs captured. For faster MCP category/tool updates after settings changes, you can tune `CHL_CATEGORIES_CACHE_TTL` (seconds, default 30).

### System requirements

- Python 3.10 or 3.11 (uv installs/interprets 3.11 automatically on first run)
- Supported OS: macOS Apple Silicon, Linux x86_64/ARM64, Windows x86_64
- Recommended hardware: 16GB+ RAM (32GB if running local embeddings); GPU optional but helpful for FAISS rebuilds
- Google Service Account credential JSON + shared review sheet
- One MCP client at a time per repo clone (export/import scripts let you merge later)

## Web dashboards (Phases 0–3)

- **Settings** – Credential helper (upload or managed path), Sheets configuration, model picker, diagnostics, audit log, and JSON backup/restore. All instructions render inline so a non-technical operator can complete setup without referencing this README.
- **Operations** – Import/export/index triggers with last-run summaries, live queue depth, job history, and FAISS snapshot upload/download. Worker controls stay hidden unless you attach an external pool. Updates stream over SSE; fall back to manual refresh if SSE is blocked.

Both dashboards share the same process as the MCP/API stack, so every change is logged and subject to the same safety constraints (locks, validation, audit trail).

## Advanced references

- Workflow philosophy: [doc/chl_guide.md](doc/chl_guide.md)
- Operator runbooks & API details: [doc/chl_manual.md](doc/chl_manual.md)
- Web plan breakdown (Phases 0–3): [doc/plan/06_web_interface/](doc/plan/06_web_interface/)
- Advanced toggle: set `CHL_OPERATIONS_MODE=noop` before starting the server if you need the Operations buttons to stay in dry-run mode (the default `scripts` mode executes the CLI helpers).

## License

[MIT](LICENSE)
