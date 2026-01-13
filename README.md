# CHL MCP Server

Curated Heuristic Loop (CHL) is a Model Context Protocol backend that helps code assistants remember what worked. Instead of forgetting between sessions, CHL keeps a shared memory of useful heuristics (experiences + skills), searchable with FAISS and reranking, and lets teams curate everything through a browser UI.

- Concept and workflow philosophy: [doc/concept.md](doc/concept.md)
- Operator guide: [doc/manual.md](doc/manual.md)
- Architecture: [doc/architecture.md](doc/architecture.md)
- Curation overview: [doc/curation.md](doc/curation.md)
- Curation spec: [doc/curation_spec.md](doc/curation_spec.md)

## Quick Start

### 0) Verify your environment

```bash
python3 scripts/setup/check_api_env.py
```
This detects your hardware and writes `data/runtime_config.json` (and `data/model_selection.json` for GPU mode). Do not proceed until it exits with code 0.

### 1) Install the API server

Platform-specific instructions live in [doc/install_env.md](doc/install_env.md).

### 2) Configure environment

```bash
cp .env.sample .env
# Edit .env and fill in:
# - GOOGLE_CREDENTIAL_PATH (Need to apply a google service account and download its credential)
# - IMPORT_SPREADSHEET_ID (Use 1svFcLFiPsxPUDyhTbJs89yrMowR2de9UWVO-q7IE0Fg to test the datapipe demo)
# - EXPORT_SPREADSHEET_ID
# - CHL_SKILLS_ENABLED (optional, default: true)
```

Notes on `CHL_SKILLS_ENABLED` and external `SKILLS.md` management are in `doc/manual.md`.

### 3) Initialize the API server

**CPU-only:**
```bash
source .venv-cpu/bin/activate
python scripts/setup/setup-cpu.py
```

**GPU modes:**
```bash
source .venv-apple/bin/activate  # Or .venv-nvidia, .venv-amd, .venv-intel
python scripts/setup/setup-gpu.py
```

This seeds all default categories from `src/common/config/categories.py` (currently 30+).

### 4) Start the API server

```bash
./start-chl.sh   # macOS/Linux
start-chl.bat    # Windows
```

Open `http://127.0.0.1:8000/settings` to verify configuration.

### 5) Configure MCP server

Most MCP clients (Codex, Claude, Cursor) start MCP servers automatically once configured.

**Canonical project-level MCP config (`.mcp.json`):**
```json
{
  "mcpServers": {
    "chl": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/curated-heuristic-loop", "run", "python", "-m", "src.mcp.server"],
      "env": {
        "UV_PROJECT_ENVIRONMENT": ".venv-mcp"
      }
    }
  }
}
```

**For ChatGPT Codex** (`~/.codex/config.toml`):
```toml
[mcp_servers.chl]
command = "uv"
args = ["--directory", "/absolute/path/to/curated-heuristic-loop", "run", "python", "-m", "src.mcp.server"]
env = { UV_PROJECT_ENVIRONMENT = ".venv-mcp" }
```

**Client config paths (summary):**
- Claude Code: `~/.claude/instructions.md` (instructions) and `.mcp.json` (project config)
- Cursor: `~/.cursor/mcp.json`
- Claude Desktop: `~/Library/Application Support/Claude/claude_desktop_config.json`
- ChatGPT Codex: `~/.codex/config.toml`

After configuration, restart your MCP client and try: "List available categories using your chl toolset".

### 6) Try the demo

See [Run Datapipe Demo](doc/manual.md#53-demo-run-datapipe-sample) for the DataPipe demo runbook.

## System Requirements

**API server:**
- CPU mode: Python 3.10-3.13
- Apple Metal: Python 3.10-3.12
- NVIDIA CUDA: Python 3.10-3.11

**MCP server:**
- Python 3.10-3.13

## Routine Operations

Use the web dashboards for most operations:
- **Settings** (`/settings`): configuration, model selection, diagnostics, audit log, JSON backup
- **Operations** (`/operations`): import/export/index triggers and job history

CLI scripts (activate API server venv first):
- `python scripts/ops/rebuild_index.py`
- `python scripts/ops/search_health.py`

## License

MIT
