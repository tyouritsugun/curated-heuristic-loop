# CHL MCP Server

Curated Heuristic Loop (CHL) is a Model Context Protocol backend that helps code assistants remember what worked. Instead of forgetting between sessions, CHL keeps a shared memory of useful heuristics, searchable with FAISS and reranking, and lets teams curate everything through a browser UI.

For the full workflow philosophy see [doc/concept.md](doc/concept.md). For setup, skills/experiences, and workflows, see the [operator guide](doc/manual.md).
For the overnight curation overview, see [doc/experience_curation.md](doc/experience_curation.md).

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
python3 scripts/setup/check_api_env.py
```

This script checks:
- GPU hardware detection (Metal/CUDA/CPU)
- Driver and toolchain availability
- VRAM capacity and model size recommendations (GPU modes only)

If checks pass:
- **GPU mode**: Writes recommended model choices to `data/model_selection.json` and runtime configuration to `data/runtime_config.json`
- **CPU mode**: Writes runtime configuration to `data/runtime_config.json` (no models needed - uses SQLite keyword search only)

If checks fail, it writes a troubleshooting prompt to `data/support_prompt.txt` – copy this text into ChatGPT/Claude and follow the steps to fix your environment before proceeding.

The API server automatically uses the backend from `runtime_config.json` - no manual configuration needed!

**Do not proceed to Step 1 until this script exits with code 0.**

**Python version note:**
- **CPU-only mode**: Python 3.10 or newer (including 3.13) - no version restrictions
- **GPU modes** (Metal/CUDA): Python 3.10–3.12 only (Python 3.13 not supported by llama-cpp-python)
  - Recommended: Python 3.11 for NVIDIA CUDA, Python 3.12 for Apple Silicon

If you have Python 3.13 and want GPU acceleration, install a compatible version (e.g., `brew install python@3.12` or `python3.12`) alongside it.

**Need a guide?** If you are not confident with Python/FAISS/GPU setup, open this repo in your preferred code assistant (Claude Code, Codex, Cursor, Gemini CLI, etc.) and ask it to walk you through the install. Some steps may still need manual permission/security approvals, but an assistant can simplify the process. This project assumes an engineering audience, so leaning on a code assistant is expected.

### LLM Access for Curation (Optional)
- Only needed if you run the overnight curation. Choose one path:
  1) **Commercial OpenAI-compatible API** (ChatGPT/Gemini): set the appropriate API key in `.env` (e.g., `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`). Use cheaper mini tiers; e.g., prefer `gpt-5-mini-2025-08-07` over `gpt-5.2-2025-12-11`, or prefer `gemini-3-flash-preview` over heavier Gemini models—the curation task doesn’t require the most expensive models.  
  2) **Local endpoint (zero API cost, requires your GPU)**: LM Studio or Ollama on an OpenAI-compatible endpoint; set `api_base` in `scripts/scripts_config.yaml` to your local server (e.g., `http://localhost:11434/v1`), and set `LLM_API_KEY` to any placeholder if your local server ignores it. `gpt-oss-20b` is recommended.  
- Keep API keys in `.env` (see `.env.sample`); do not commit them.
- Dependencies: `requirements_apple.txt` and `requirements_nvidia.txt` already include `autogen` + `autogen-ext[openai]` for the agent.

**Overnight curation defaults (config-driven):**
- `curation_llm.llm_response_timeout` (seconds per LLM call)
- `curation.thresholds.auto_dedup` (auto-merge threshold)
See `doc/experience_curation.md` for the simplified runbook.

### Step 1: Install API Server

Choose your hardware platform and install the API server runtime:

<details>
<summary><b>Option A: CPU-Only Mode (No ML Dependencies)</b></summary>

**Best for:** Limited VRAM, keyword search is sufficient, or testing without GPU overhead.

**Prerequisites:**

Python 3.10 or newer (3.11+ recommended, Python 3.13 is supported for CPU mode). Install instructions by platform:

<details>
<summary>macOS (Intel or Apple Silicon)</summary>

```bash
# Check your current Python version
python3 --version

# If you need a newer version, install via Homebrew
brew install python@3.13
# Or for older stable versions: brew install python@3.12 or python@3.11
```
</details>

<details>
<summary>Linux (Ubuntu/Debian)</summary>

```bash
# Check available versions
ls /usr/bin/python3.1*

# Ubuntu 24.04+ (has Python 3.12, or install 3.13 via deadsnakes)
sudo apt update
sudo apt install python3.12 python3.12-venv
# Or for Python 3.13:
# sudo add-apt-repository ppa:deadsnakes/ppa
# sudo apt update
# sudo apt install python3.13 python3.13-venv

# Ubuntu 22.04 (has Python 3.10, use it or upgrade)
sudo apt update
sudo apt install python3.10 python3.10-venv

# Install any specific version via deadsnakes PPA
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.11 python3.11-venv  # Or python3.12, python3.13
```
</details>

<details>
<summary>Windows</summary>

1. Download Python 3.11, 3.12, or 3.13 from [python.org](https://www.python.org/downloads/)
2. During installation, check "Add Python to PATH"
3. Verify: `python --version` in Command Prompt or PowerShell
</details>

**Installation:**

```bash
# macOS/Linux: Create dedicated venv for API server
python3 -m venv .venv-cpu
# Activate venv (only needed once per terminal session)
source .venv-cpu/bin/activate

# Windows: Create dedicated venv for API server
python -m venv .venv-cpu
# Activate venv (only needed once per terminal session)
.venv-cpu\Scripts\activate

# All platforms: Install dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements_cpu.txt
```

**Note:** CPU mode supports Python 3.13 since it has no ML dependencies.

**Note:** Search will use SQLite text search (LIKE queries) instead of semantic similarity. Good for exact phrase searches but won't find conceptually related entries.

</details>

<details>
<summary><b>Option B: Apple Silicon (Metal GPU Acceleration)</b></summary>

**Best for:** macOS with M1/M2/M3, want semantic search with GPU acceleration (HF embeddings + HF reranker).

**Prerequisites:**
- macOS with Apple Silicon (M1, M2, M3, etc.)
- Xcode Command Line Tools: `xcode-select --install`
- Python 3.12 installed (for example via Homebrew: `brew install python@3.12`)

```bash
# Create dedicated venv for API server (Python 3.12)
python3.12 -m venv .venv-apple
# Activate venv (only needed once per terminal session)
source .venv-apple/bin/activate

# Install API server dependencies (HF embeddings + HF reranker)
python -m pip install --upgrade pip
python -m pip install -r requirements_apple.txt

# Continue with Step 3 to download models and initialize the database, then Step 4 to start the server.

**Notes:**
- Default model choice (via `scripts/setup/check_api_env.py` → `scripts/setup/setup-gpu.py`) is Qwen3-Embedding-0.6B (HF) and Qwen3-Reranker-0.6B (HF) for speed on Metal.

</details>

<details>
<summary><b>Option C: NVIDIA GPU Acceleration (CUDA, HF stack)</b></summary>

**Best for:** Linux/Windows with NVIDIA GPU (Pascal or newer), want semantic search with GPU acceleration using HuggingFace Transformers + Torch CUDA.

**Prerequisites & VRAM sizing:**
- NVIDIA GPU with CUDA Compute Capability 6.0+ (Pascal or newer: GTX 1060+, RTX series, etc.)
- CUDA Toolkit 12.x installed (e.g., `/usr/local/cuda-12.4` or `/usr/local/cuda-12.5`)
- cuDNN libraries
- CMake 3.18+
- **Python 3.10 or 3.11** (Torch CUDA wheels are published for 3.10/3.11; 3.12 support may lag)
- Install if needed:
  - Ubuntu 22.04: `sudo apt install python3.10 python3.10-venv`
  - Other: Add deadsnakes PPA: `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update && sudo apt install python3.11 python3.11-venv`
- VRAM guide for HF models:
  - ≤10 GB: keep defaults (Embedding 0.6B + Reranker 0.6B)
  - 12–16 GB: Embedding 4B + Reranker 0.6B (better recall, safe VRAM)
  - ≥20 GB: Embedding 4B + Reranker 4B (highest quality)

```bash
# Create dedicated venv for API server (Python 3.10 or 3.11)
/usr/bin/python3.11 -m venv .venv-nvidia  # Or python3.10
source .venv-nvidia/bin/activate          # Windows: .venv-nvidia\Scripts\activate

# Install API server dependencies (HF + Torch CUDA)
python -m pip install --upgrade pip
PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cu124 \
  python -m pip install -r requirements_nvidia.txt

# Continue with Step 3 to download models (HF, no GGUF) and initialize the database.
```

**Troubleshooting:** If `python3.11 -m venv` fails with ensurepip errors and you have conda/uv installed, use the full system path `/usr/bin/python3.11` instead of just `python3.11` to avoid PATH conflicts.

</details>

<details>
<summary><b>Option D: AMD GPU Acceleration</b> (TBD)</summary>

**Best for:** Linux with AMD GPU (RDNA2 or newer), want semantic search with GPU acceleration.

**Status:** Requirements file and installation instructions to be added in future release.

**Prerequisites (planned):**
- AMD GPU with ROCm support (RX 6000 series, RX 7000 series, etc.)
- ROCm 5.x or 6.x installed
- hipBLAS libraries
- CMake 3.18+

**Planned installation:**
```bash
# TBD: requirements_amd.txt to be created
python -m venv .venv-amd
source .venv-amd/bin/activate
pip install -r requirements_amd.txt  # Not yet available
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
# - IMPORT_SPREADSHEET_ID (if you want to try the sample of DataPipe, keep the ID in .env.sample, overwrite it when necessary)
# - EXPORT_SPREADSHEET_ID (review spreadsheet ID for exports)
```

### Step 3: Initialize API Server

**For CPU-only mode:**
```bash
# Activate API server venv (if not already activated from Step 1)
source .venv-cpu/bin/activate

# Initialize database with default categories
python scripts/setup/setup-cpu.py
```

This seeds 12 default categories (TMG, PGS, etc.). The TMG category includes sample DataPipe bug reporting guidance for the optional demo (see Step 7).

**For GPU modes (Apple Metal, NVIDIA CUDA, AMD Rocm, or Intel oneAPI):**
```bash
# Activate API server venv (if not already activated from Step 1)
source .venv-apple/bin/activate  # Or .venv-nvidia, .venv-amd, .venv-intel

# Download models and initialize database using recommended/active models
python scripts/setup/setup-gpu.py

# (Optional) Open interactive model selection menu
python scripts/setup/setup-gpu.py --select-models
```

**Note for Users with Restricted Internet Access:**
This system requires access to Hugging Face to download and load the embedding models needed for semantic search and curation workflows. Without access to Hugging Face, the system will use the local cache, if exists. if no exists, it will fall back to basic text search only, and advanced curation features that require semantic similarity (as described in doc/curation_sample.md) will not be available, although the Export Excel would be possible.

### Step 4: Start API Server

**If continuing from Step 3 in the same terminal session:**
```bash
# Your venv is already activated, just start the server:
python -m uvicorn src.api.server:app --host 127.0.0.1 --port 8000
```

**If starting fresh (after reboot, new terminal, or subsequent runs):**
```bash
# macOS/Linux: Run the startup script (handles activation automatically)
./start-chl.sh

# Windows: Run the startup script (handles activation automatically)
start-chl.bat
```

The startup script automatically detects your installed venv (CPU, Apple Metal, NVIDIA, etc.) and starts the API server on http://127.0.0.1:8000.

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

### Step 6: Install MCP Server

The MCP server allows AI assistants to interact with CHL. It communicates with the API server via HTTP.

**Prerequisites:**
- API server must be running at `http://localhost:8000`
- Install [uv](https://docs.astral.sh/uv/) if not already installed:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

**Configure MCP client** (e.g., Claude Code, Claude Desktop, Cursor, ChatGPT Codex):

Add to your MCP configuration file:

**For Claude Code** - Choose one of these configuration scopes:

*Option 1: User-level (recommended)* - Available across all projects on your machine:
```bash
claude mcp add \
  --scope user \
  --transport stdio \
  --env UV_PROJECT_ENVIRONMENT=.venv-mcp \
  chl \
  -- \
  uv --directory /absolute/path/to/curated-heuristic-loop run python -m src.mcp.server
```

*Option 2: Project-level* - Create `.mcp.json` in the project root (good for team sharing):
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

> **Important**: After adding the MCP server via command or creating `.mcp.json`, restart Claude Code/Cursor for the MCP server to be recognized. User-scope configuration avoids reconfiguring for each project.

**For Cursor** (`~/.cursor/mcp.json`) or **Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):
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
If `uv` complains about permissions in `~/.cache/uv`, set `UV_CACHE_DIR` in `env` to a writable path (for example the project root); otherwise keep it minimal as above.

**Configure agent instructions:**

To keep assistants from forgetting to call MCP tools and to prompt for reflections, configure CHL instructions for your code assistant:

- **Recommended approach:** Ask your code assistant: "Please copy AGENTS.md.sample to the appropriate location so you can read it automatically in this project"
  - For example, Claude Code uses `.claude/instructions.md`, Cursor may use project-level configuration, etc.
  - Different code assistants have different configuration systems - letting the assistant handle this ensures it uses the right approach for its platform
  - The assistant will know where to copy and whether needs to rename it to following the convention.

These CHL instructions ensure the assistant:
- Calls `list_categories()` and `get_guidelines()` at startup
- Uses CHL MCP tools for retrieval instead of guessing
- Prompts for reflection and curation at conversation end

**Note:** All environment variables have defaults and are optional:
- `CHL_API_BASE_URL` defaults to `http://localhost:8000`
- `CHL_EXPERIENCE_ROOT` defaults to `<project>/data` (auto-created if missing)
- `CHL_DATABASE_PATH` defaults to `<experience_root>/chl.db`

Only add `env` section if you need non-default values.

**Test MCP integration:**
- Restart your MCP client (Cursor, Claude Code, etc.)
- Try: "List available categories"
- Try: "Search for entries about bug reporting"

### Step 7: Try the Demo (Optional)

CHL includes a demo that shows how it teaches LLMs project-specific conventions. The demo uses a fictional "DataPipe" project to demonstrate the difference between generic bug reporting vs. team-specific ticket requirements.

**What the demo shows:**
- **Without CHL**: LLM rushes to fix code and writes incomplete tickets missing required artifacts
- **With CHL**: LLM clarifies intent first and enforces project-specific ticket format (Run ID, pipeline stage, logs)

**Full instructions:** See [doc/run_sample.md](doc/run_sample.md) for complete demo guide with A/B testing steps, expected behaviors, and troubleshooting.

## Mode Switching

To switch between CPU and GPU modes:
1. Stop the API server
2. Run `python scripts/setup/check_api_env.py` and select target mode (updates `runtime_config.json`)
3. Create new venv for target mode and install corresponding requirements file
4. GPU mode only: Run `python scripts/setup/setup-gpu.py --download-models` and rebuild embeddings via `/operations`
5. Start API server (automatically uses backend from `runtime_config.json`)

> **Note**: FAISS snapshots are not portable between modes. Switching requires rebuilding search index in the target mode.

## Routine Operations

After initial installation, use the web dashboards for most operations:

- **Import/Export**: Use `/operations` dashboard to sync with Google Sheets or rebuild search index
- **Settings**: Use `/settings` dashboard for configuration, model selection, and JSON backup

**CLI scripts** (activate API server venv first: `source .venv-cpu/bin/activate`):
- `python scripts/ops/rebuild_index.py` – rebuild search index if needed
- `python scripts/ops/search_health.py` – check search system health

## Managing Categories

CHL comes with 12 default categories (TMG, PGS, ADG, etc.) seeded during setup. To add custom categories for your team's workflows:

**Quick steps:**
1. Export current database via Settings → "Export Spreadsheet"
2. Save export as timestamped backup in Google Drive (recommended)
3. Copy Categories worksheet to your import spreadsheet
4. Add new row with `code`, `name`, and `description`
5. Import via Operations → "Run Import"

**Example categories:**
- `DEP` / `deployment_procedures` - Deployment checklists and rollback procedures
- `SEC` / `security_review` - Security review patterns and vulnerability checks
- `ONC` / `oncall_runbook` - Incident response and on-call procedures

**Full instructions:** See [Managing Categories in the Operator Guide](doc/manual.md#62-managing-categories) for detailed steps, best practices, and troubleshooting.


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
- [Operator guide](doc/manual.md) (setup, skills/experiences, API details)
- Architecture design and ADRs: [doc/architecture.md](doc/architecture.md)
- Architecture refinement roadmap: [doc/plan/architecture_refine.md](doc/plan/architecture_refine.md)

## License

[MIT](LICENSE)
