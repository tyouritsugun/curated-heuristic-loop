# CHL MCP Server

Curated Heuristic Loop (CHL) is a Model Context Protocol backend that helps code assistants remember what worked. Instead of forgetting between sessions, CHL keeps a shared memory of useful heuristics, searchable with FAISS and reranking, and lets teams curate everything through a browser UI.

For the full workflow philosophy see [doc/concept.md](doc/concept.md). For detailed operator procedures see [doc/manual.md](doc/manual.md).

## Architecture Overview

CHL uses a two-tier architecture with clear separation of concerns:

1. **API Server** (`src/api/`): FastAPI server that handles all data persistence (SQLite), search operations (FAISS/text), and background workers. Platform-specific installation for CPU-only, Apple Metal, NVIDIA CUDA, or other GPUs.

2. **MCP Server** (`src/mcp/`): Lightweight HTTP client that exposes CHL functionality to AI assistants via the Model Context Protocol. Installed separately via `uv sync`.

The API and MCP servers communicate exclusively via HTTP (default: `http://localhost:8000`). All operational scripts (import, export, rebuild index) run from the API server's environment.

**For detailed architecture diagrams and design decisions, see [doc/architecture.md](doc/architecture.md).**

## Quick Start

### Step 0: Verify Your Environment

Before installing the API server, run the environment diagnostics script to validate your hardware and toolchain:

```bash
python3 scripts/check_api_env.py
```

This script checks:
- GPU hardware detection (Metal/CUDA/CPU)
- Driver and toolchain availability
- VRAM capacity and model size recommendations
- llama-cpp-python wheel compatibility (via the official wheel index)

If checks pass, it writes recommended model choices to `data/model_selection.json` and prints a suggested `CHL_SEARCH_MODE` value.  
If checks fail, it writes a troubleshooting prompt to `data/support_prompt.txt` – copy this text into ChatGPT/Claude and follow the steps to fix your environment before proceeding.

**Do not proceed to Step 1 until this script exits with code 0.**

**Python version note:** CHL currently targets CPython 3.10–3.12. **We recommend Python 3.11 for most platforms** (CPU-only and NVIDIA CUDA) and **Python 3.12 for Apple Silicon**. Python 3.13 is not yet supported by some dependencies (for example, NumPy 1.x), and `pip` may try – and fail – to build them from source. If `python --version` shows 3.13, use a specific compatible interpreter (for example, `python3.12` or `python3.11`) in all commands below when creating and using virtual environments.

### Step 1: Install API Server

Choose your hardware platform and install the API server runtime:

<details>
<summary><b>Option A: CPU-Only Mode (No ML Dependencies)</b></summary>

**Best for:** Limited VRAM, keyword search is sufficient, or testing without GPU overhead.

**Prerequisites:**
- Python 3.10 or 3.11 (check available versions: `ls /usr/bin/python3.1*`)
- Install if needed:
  - Ubuntu 24.04+: `sudo apt install python3.12-venv` (use python3.12)
  - Ubuntu 22.04: `sudo apt install python3.10-venv` (use python3.10)
  - Other: `sudo apt install python3.11 python3.11-venv` (recommended)

```bash
# Create dedicated venv for API server (use your available Python 3.10/3.11/3.12)
python3.11 -m venv .venv-cpu  # Or python3.10 or python3.12
source .venv-cpu/bin/activate  # On Windows: .venv-cpu\Scripts\activate

# Install API server dependencies (no ML)
python -m pip install --upgrade pip
python -m pip install -r requirements_cpu.txt
```

**Note:** Search will use SQLite text search (LIKE queries) instead of semantic similarity. Good for exact phrase searches but won't find conceptually related entries.

</details>

<details>
<summary><b>Option B: Apple Silicon (Metal GPU Acceleration)</b></summary>

**Best for:** macOS with M1/M2/M3, want semantic search with GPU acceleration.

**Prerequisites:**
- macOS with Apple Silicon (M1, M2, M3, etc.)
- Xcode Command Line Tools: `xcode-select --install`
- Python 3.12 installed (for example via Homebrew: `brew install python@3.12`)

```bash
# Create dedicated venv for API server (Python 3.12)
python3.12 -m venv .venv-apple
source .venv-apple/bin/activate

# Install API server dependencies with Metal-accelerated ML
python -m pip install --upgrade pip
PIP_EXTRA_INDEX_URL=https://abetlen.github.io/llama-cpp-python/whl/metal \
  python -m pip install -r requirements_apple.txt
```

</details>

<details>
<summary><b>Option C: NVIDIA CUDA GPU Acceleration</b></summary>

**Best for:** Linux/Windows with NVIDIA GPU (Pascal or newer), want semantic search with CUDA acceleration.

**Prerequisites:**
- NVIDIA GPU with CUDA Compute Capability 6.0+ (Pascal or newer: GTX 1060+, RTX series, etc.)
- CUDA Toolkit 12.x installed (e.g., `/usr/local/cuda-12.5`)
- cuDNN libraries
- CMake 3.18+
- **Python 3.10 or 3.11** (CUDA wheels don't support Python 3.12 yet)
- Install if needed:
  - Ubuntu 22.04: `sudo apt install python3.10 python3.10-venv`
  - Other: Add deadsnakes PPA: `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update && sudo apt install python3.11 python3.11-venv`

```bash
# Create dedicated venv for API server (Python 3.10 or 3.11, NOT 3.12)
# Use full path if you have conda/uv Python that conflicts:
/usr/bin/python3.11 -m venv .venv-cuda  # Or python3.11 if no PATH conflicts
source .venv-cuda/bin/activate  # On Windows: .venv-cuda\Scripts\activate

# Install API server dependencies with CUDA-accelerated ML (abetlen wheels)
python -m pip install --upgrade pip
PIP_EXTRA_INDEX_URL=https://abetlen.github.io/llama-cpp-python/whl/cuda \
  python -m pip install -r requirements_cuda.txt
```

**Troubleshooting:** If `python3.11 -m venv` fails with ensurepip errors and you have conda/uv installed, use the full system path `/usr/bin/python3.11` instead of just `python3.11` to avoid PATH conflicts.

</details>

<details>
<summary><b>Option D: AMD ROCm GPU Acceleration</b> (TBD)</summary>

**Best for:** Linux with AMD GPU (RDNA2 or newer), want semantic search with ROCm acceleration.

**Status:** Requirements file and installation instructions to be added in future release.

**Prerequisites (planned):**
- AMD GPU with ROCm support (RX 6000 series, RX 7000 series, etc.)
- ROCm 5.x or 6.x installed
- hipBLAS libraries
- CMake 3.18+

**Planned installation:**
```bash
# TBD: requirements_rocm.txt to be created
python -m venv .venv-rocm
source .venv-rocm/bin/activate
pip install -r requirements_rocm.txt  # Not yet available
```

</details>

<details>
<summary><b>Option E: Intel GPU Acceleration</b> (TBD)</summary>

**Best for:** Linux/Windows with Intel Arc or integrated GPU, want semantic search with oneAPI acceleration.

**Status:** Requirements file and installation instructions to be added in future release.

**Prerequisites (planned):**
- Intel Arc GPU or integrated graphics with oneAPI support
- Intel oneAPI Base Toolkit installed
- oneMKL libraries
- CMake 3.18+

**Planned installation:**
```bash
# TBD: requirements_intel.txt to be created
python -m venv .venv-intel
source .venv-intel/bin/activate
pip install -r requirements_intel.txt  # Not yet available
```

</details>

### Step 2: Configure Environment

Apply the google service account and download the json credential file.
Prepare the google spreadsheets for import and export, and share them with the account in your google service credential file with read and write permission.

```bash
cp .env.sample .env
# Edit .env and fill in:
# - GOOGLE_CREDENTIAL_PATH (path to your service account JSON)
# - IMPORT_SPREADSHEET_ID (published spreadsheet ID for imports)
# - EXPORT_SPREADSHEET_ID (review spreadsheet ID for exports)
# - CHL_SEARCH_MODE (cpu for CPU-only, auto for GPU with fallback)
```

### Step 3: Initialize API Server

**For CPU-only mode:**
```bash
# Activate API server venv
source .venv-cpu/bin/activate

# Initialize database (no models needed)
CHL_SEARCH_MODE=cpu python scripts/setup-cpu.py
```

**For GPU modes (Apple Metal or NVIDIA CUDA):**
```bash
# Activate API server venv
source .venv-apple/bin/activate  # Or .venv-cuda

# Download models and initialize database using recommended/active models
python scripts/setup-gpu.py

# (Optional) Open interactive model selection menu
python scripts/setup-gpu.py --select-models
```

### Step 4: Start API Server

**CPU-only mode:**
```bash
source .venv-cpu/bin/activate
CHL_SEARCH_MODE=cpu python -m uvicorn src.api.server:app --host 127.0.0.1 --port 8000
```

**GPU modes (Apple Metal or NVIDIA CUDA):**
```bash
source .venv-apple/bin/activate  # Or .venv-cuda
CHL_SEARCH_MODE=auto python -m uvicorn src.api.server:app --host 127.0.0.1 --port 8000
```

**Verify installation:**
- Open http://127.0.0.1:8000/settings to verify configuration
- Test connection to validate Google Sheets access
- Review model selection and system diagnostics (GPU modes)
- Download JSON backup once everything looks good

### Step 5: Run Initial Import

Open http://127.0.0.1:8000/operations to run import:
- Click **Run Import** to pull data from Google Sheets
- **GPU modes**: Background worker automatically processes pending embeddings and updates FAISS index
- **CPU mode**: Import completes immediately (no embeddings generated)

### Step 6: Install MCP Server (Optional)

The MCP server allows AI assistants to interact with CHL. It communicates with the API server via HTTP.

**Prerequisites:**
- API server must be running at `http://localhost:8000`
- Install [uv](https://docs.astral.sh/uv/) if not already installed:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

**Install MCP server:**
```bash
# In a new terminal (keep API server running)
cd /path/to/curated-heuristic-loop
uv sync --python 3.11
```

**Configure MCP client** (e.g., Claude Code, Claude Desktop, Cursor, ChatGPT Codex):

Add to your MCP configuration file:

**For Claude Code** - Choose one of these configuration scopes:

*Option 1: User-level (recommended)* - Available across all projects on your machine:
```bash
claude mcp add --scope user --transport stdio chl -- uv --directory /absolute/path/to/curated-heuristic-loop run python -m src.mcp.server
```

*Option 2: Project-level* - Create `.mcp.json` in the project root (good for team sharing):
```json
{
  "mcpServers": {
    "chl": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/curated-heuristic-loop", "run", "python", "-m", "src.mcp.server"]
    }
  }
}
```

> **Important**: After adding the MCP server via command or creating `.mcp.json`, restart Claude Code/Cursor for the MCP server to be recognized. User-scope configuration avoids reconfiguring for each project.

**For Cursor** (`~/.cursor/mcp.json`) or **Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):
```json
{
  "mcpServers": {
    "chl": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/curated-heuristic-loop", "run", "python", "-m", "src.mcp.server"]
    }
  }
}
```

**For ChatGPT Codex** (`~/.codex/config.toml`):
```toml
[mcp_servers.chl]
command = "uv"
args = ["--directory", "/absolute/path/to/curated-heuristic-loop", "run", "python", "-m", "src.mcp.server"]
```

**Note:** All environment variables have defaults and are optional:
- `CHL_API_BASE_URL` defaults to `http://localhost:8000`
- `CHL_EXPERIENCE_ROOT` defaults to `<project>/data` (auto-created if missing)
- `CHL_DATABASE_PATH` defaults to `<experience_root>/chl.db`

Only add `env` section if you need non-default values.

**Test MCP integration:**
- Restart your MCP client (Cursor, Claude Code, etc.)
- Try: "List available categories"
- Try: "Search for entries about error handling"

## Mode Switching

**Switching from CPU-only to GPU mode:**
1. Stop the API server
2. Set `CHL_SEARCH_MODE=auto` in `.env`
3. Create new GPU venv (`.venv-apple` or `.venv-cuda`) and install corresponding requirements file
4. Run `python scripts/setup-gpu.py --download-models`
5. Start API server with GPU mode
6. Rebuild embeddings/FAISS via `/operations` or `scripts/rebuild_index.py`

**Switching from GPU to CPU-only mode:**
1. Stop the API server
2. Set `CHL_SEARCH_MODE=cpu` in `.env`
3. Create new CPU venv (`.venv-cpu`) and install `requirements_cpu.txt`
4. Start API server with CPU mode
5. FAISS artifacts remain on disk but are ignored

> **Important**: FAISS snapshots are NOT portable between modes. Switching modes requires rebuilding from scratch in the target mode.

## Operational Scripts

All scripts run from the API server's venv (NOT via `uv run`):

**Activate API server venv first:**
```bash
source .venv-cpu/bin/activate  # Or .venv-apple / .venv-cuda
```

**Then run scripts:**
- `python scripts/seed_default_content.py` – idempotently loads starter categories and sample experiences
- `python scripts/export.py` – pushes local SQLite data to Google Sheets (uses `scripts/scripts_config.yaml`)
- `python scripts/import.py --yes` – pulls from Sheets (coordinates with worker pool in GPU mode)
- `python scripts/rebuild_index.py` – rebuilds FAISS index (GPU mode) or SQLite FTS (CPU mode)
- `python scripts/sync_embeddings.py` – syncs embeddings for all entries (GPU mode only)
- `python scripts/search_health.py` – checks search system health

> **Note**: Scripts use the API server's HTTP endpoints when possible. Setup scripts (`setup-gpu.py`, `gpu_smoke_test.py`) are exceptions that access internal components directly and must run with the API server stopped.

## Web Dashboards

Access these dashboards while the API server is running:

- **Settings** (`/settings`) – First-time checklist, configuration loader, model picker, diagnostics, audit log, and JSON backup/restore
- **Operations** (`/operations`) – Import/export/index triggers, live queue depth, job history, and FAISS snapshot upload/download

Both dashboards share the same process as the API server, so every change is logged and subject to the same safety constraints (locks, validation, audit trail).

## System Requirements

- Python 3.10 or 3.11 (API server and MCP server)
- Supported OS: macOS Apple Silicon, Linux x86_64/ARM64, Windows x86_64
- Recommended hardware:
  - **CPU mode**: 8GB+ RAM
  - **GPU mode**: 16GB+ RAM (32GB for large datasets), GPU with 8GB+ VRAM
- Google Service Account credential JSON + shared review sheet
- One MCP client at a time per repo clone (export/import scripts let you merge later)

## Advanced References

- Workflow philosophy: [doc/concept.md](doc/concept.md)
- Operator runbooks & API details: [doc/manual.md](doc/manual.md)
- Architecture design and ADRs: [doc/architecture.md](doc/architecture.md)
- Web plan breakdown (Phases 0–3): [doc/plan/06_web_interface/](doc/plan/06_web_interface/)

## License

[MIT](LICENSE)
