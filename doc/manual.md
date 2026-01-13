[Return to README.md](../README.md)
# CHL Operator Guide

This guide covers the setup, daily workflows, and operational tasks for the Curated Heuristic Loop (CHL) system. For the project's philosophy, see [concept.md](./concept.md), and for technical details, see [architecture.md](./architecture.md).

> **Note:** CHL uses the term "skill" to refer to comprehensive workflow procedures. In earlier versions, these were called "manuals." If you see legacy references to "manual" in code or older documentation, they refer to the same concept now called "skill."

## 1. Initial Setup

This section guides you through the first-time setup of the CHL environment.

### 1.1. Quick Start
For step-by-step installation, follow the **Quick Start** in [README.md](../README.md). This guide focuses on day-to-day operations; only a condensed install summary is included here.

**Choose your installation mode:**

- **GPU mode** (recommended for semantic search): Use the API server venv with the platform requirements file (`requirements_apple.txt` or `requirements_nvidia.txt`) for FAISS + embeddings. 
- **CPU-only mode** (keyword search): Use the API server venv with `requirements_cpu.txt` for SQLite text search (no ML dependencies).

**Decision guidance:**

- Have ≥6 GB VRAM and need semantic search? → GPU mode
- Limited VRAM or keyword search is sufficient? → CPU-only mode
- Can switch modes later, but FAISS snapshots are NOT portable between modes

See the [CPU-Only Mode](#9-cpu-only-mode) section below for details on running CHL without ML dependencies.

### 1.2. First-Time Setup Script

The setup scripts initialize your local environment. Choose the appropriate script based on your setup:

- `scripts/setup/setup-gpu.py`: For GPU-enabled systems with vector search (downloads ML models)
- `scripts/setup/setup-cpu.py`: For CPU-only systems using SQLite keyword search (no ML dependencies)

**Command (GPU mode):**
```bash
# In the API server venv you created with requirements_apple.txt or requirements_nvidia.txt
python scripts/setup/setup-gpu.py
```

**What GPU setup does:**

1. Creates the `data/` directory structure
2. Initializes the SQLite database (`chl.db`)
3. Downloads the required embedding and reranker models
4. Creates the FAISS index directory
5. Validates model availability

**Command (CPU-only mode):**
```bash
# In the API server venv you created with requirements_cpu.txt
python scripts/setup/setup-cpu.py
```

**What CPU-only setup does:**

1. Creates the `data/` directory (no FAISS directory)
2. Initializes the SQLite database (`chl.db`)
3. Seeds default categories and sample entries
4. Validates credential paths (non-fatal if missing)

**When to use:**

- After first cloning the repository
- If your `data/` directory is deleted or corrupted
- To re-download models after changing selection (GPU mode only)

### 1.3. Import Default Content

After setup, import the default content (sample entries) from Google Sheets:

1. Ensure `IMPORT_SPREADSHEET_ID` is set in your `.env` (use the value from `.env.sample` for demo data).
   - DataPipe demo sample ID: `1svFcLFiPsxPUDyhTbJs89yrMowR2de9UWVO-q7IE0Fg`
2. Start the API server: `uvicorn src.api.server:app --host 127.0.0.1 --port 8000`
3. Open the Settings dashboard: http://127.0.0.1:8000/settings
4. Click **Import Spreadsheet**

This operation imports data from the configured Google Sheet, including:

- Sample entries (bug reporting guidance, etc.)
- SQLite database population
- Embedding generation and FAISS indexing (GPU mode only)

Categories are seeded from the code-defined taxonomy, and guidelines are read directly from `generator.md` / `evaluator.md` / `evaluator_cpu.md`.

## 2. Understanding CHL's Knowledge Structure

Before diving into workflows, it's essential to understand how CHL organizes knowledge using three core concepts: **categories**, **skills**, and **experiences**.

### 2.1. Categories: Knowledge Boundaries

Categories are organizational "shelves" that isolate knowledge by domain or workflow type. Think of them like library sections—each category contains skills and experiences relevant to a specific area of work.

**Examples:**
- `TMG` (Ticket Management): Bug reporting, issue tracking conventions
- `PGT` (Python Agent): AI assistant development patterns
- `LPW` (Laravel PHP Web): PHP web application best practices
- `FPD` (Figma Page Design): Design workflow guidance

**Purpose:**
- Prevent knowledge pollution (e.g., PHP conventions don't leak into Python agent guidance)
- Enable targeted search (query only relevant categories)
- Support team specialization (frontend team uses different categories than backend)

**When to create a category:**
- You have a distinct workflow or technology domain
- Knowledge in this area doesn't naturally fit existing categories
- Multiple team members work in this domain

See [Managing Categories](#62-managing-categories) for how to add custom categories.

### 2.2. Skills: Workflow-Level Guidance

Skills are **process-oriented playbooks** that describe complete workflows or multi-step procedures. They answer "How do we do X from start to finish?"

**Characteristics:**
- Cover end-to-end processes (e.g., "Bug Report Template", "API Design Review Checklist")
- Structured with sections, steps, or decision trees
- Updated when the team's process evolves
- Typically 1-3 pages long

**Example - Bug Report Template (TMG category):**
```markdown
# Bug Report Template

## Required Artifacts
1. Run ID (from metadata JSON)
2. Pipeline stage (transform/validate/load)
3. Log excerpt with error message
4. Expected vs actual behavior

## Ticket Format
- Title: [STAGE] Brief description
- Body: Include Run ID, logs, reproduction steps
```

**When to write a skill:**
- You're documenting a repeatable process
- The workflow has multiple steps or decision points
- New team members need a reference guide

### 2.3. Experiences: Atomic Learnings

Experiences are **single, actionable heuristics**—small lessons learned from real work. They answer "What's one thing I should remember when doing X?"

**Characteristics:**

- Atomic (one insight per entry)
- Action-oriented (prescriptive, not descriptive)
- Context-specific (tied to real situations)
- Short (1-3 sentences or bullet points)

**Examples (TMG category):**

- "Always check for Run ID in metadata JSON before filing pipeline bugs"
- "When user reports a bug, clarify intent first: fix code, file ticket, or investigate?"
- "Look for `data/output/app.log` for pipeline error stack traces"

**When to write an experience:**

- You learned something specific during a task
- It's a "gotcha" or non-obvious insight
- You want to remember this for next time

### 2.4. How They Work Together

**Category** → Contains both skills and experiences about a specific domain

**Skill** → References or is informed by multiple experiences

**Experience** → Atomic building blocks that can later be synthesized into skills

**Example hierarchy:**
```
Category: TMG (Ticket Management)
├── Skill: "Bug Report Template"
│   └── Synthesized from experiences about required artifacts, formatting, etc.
├── Experience: "Always check for Run ID in metadata JSON"
├── Experience: "Clarify user intent before rushing to fix code"
└── Experience: "Pipeline logs live in data/output/app.log"
```

**User Story:**

1. During tasks, assistant captures **experiences** (atomic learnings)
2. Over time, related experiences are synthesized into **skills** (playbooks)
3. Both are organized by **category** (domain boundaries)
4. Curator reviews and publishes the highest-quality entries
5. Team imports published knowledge into their local databases

## 3. The CHL Workflow

The CHL workflow is designed for developers, AI assistants, and curators to collaborate on building a shared knowledge base.

### 3.1. End-to-End Workflow

1.  **Capture (Developer & Assistant):** During a work session, the assistant uses existing knowledge (`read_entries`). Afterwards, the assistant reflects on the session (`create_entry`), capturing new insights as either atomic experiences or updates to skills. These new entries are saved to the local SQLite database with a `pending` status.
2.  **Vector Refresh (Operator):** To keep search fast and accurate, an operator periodically regenerates the vector index. This is done via the **Web UI** or by running `scripts/ops/rebuild_index.py`. This process generates embeddings for all `pending` entries.
3.  **Export for Review (Curator):** A curator exports all `pending` entries from the team's local databases into a shared Google Sheet using the API server's Operations dashboard (or `GET /api/v1/entries/export` for automation).
4.  **Curate (Curator):** The curator reviews the submitted entries in Google Sheets, merging duplicates, editing for clarity, and approving the highest-quality insights.
5.  **Distribute (Developer):** Developers sync their local databases from the Published Sheet using the API server import job (`/operations` dashboard or `POST /api/v1/operations/import-sheets`). This updates their local knowledge base with the latest curated heuristics.

### 3.2. MCP Interaction Flow (for Assistant developers)

1.  **Startup:** The MCP service loads its configuration and advertises available categories via `list_categories`.
2.  **Generator Mode:** The assistant queries for relevant entries using `read_entries(query=...)`.
3.  **Evaluator Mode:** The assistant writes new knowledge using `create_entry(...)`, which returns similarity scores to help decide whether to create, update, or refactor an entry.
4.  **Knowledge scope:** CHL stores skills and experiences (shared heuristics), not domain- or product-specific content (e.g., no customer-specific page designs). Treat the KB as generic process/UX/code heuristics organized by category.

### 3.3. Review and Governance

-   **Controlled Vocabulary:** Use a consistent set of categories, sections, and tags.
-   **Curator Actions:** Curators should record actions (e.g., `accepted`, `superseded`) in the Published Sheet to provide feedback to the system.
-   **Analytics:** Periodically run analytics on the knowledge base to identify unused entries or duplicate clusters.

## 4. Web UI Guide

**Note:** For daily work, you don't need to use the web interface. CHL handles most operations automatically (capturing entries via MCP, embedding generation, search). The web UI is primarily used for:
- **Initial setup**: Configuring credentials and importing default content
- **Team coordination**: Importing/exporting Google Sheets for curation

The web interface is available by running `uvicorn src.api.server:app` and navigating to `http://127.0.0.1:8000`.

**CPU vs GPU differences:**
- CPU mode: No model selection (no ML dependencies), simpler interface (no worker status, no FAISS snapshots)
- GPU mode: Model selection for embedding and reranker models, full control over embedding worker and FAISS operations

## 5. Command-Line Operations

For automation and scripting, activate the API server venv first, then use these tools.

### 5.1. Search and Indexing
-   **Rebuild Search Index:** Regenerates embeddings and the FAISS index from scratch.
    ```bash
    python scripts/ops/rebuild_index.py
    ```
-   **Check Search Health:** Inspects the status of the search index and embeddings.
    ```bash
    python scripts/ops/search_health.py
    ```

### 5.2. Data Synchronization
Before running, ensure your `scripts/scripts_config.yaml` is configured with the correct Google Sheet IDs.

-   **Export for review:** From a running API server, click **Export Spreadsheet**.
    - If `CHL_SKILLS_ENABLED=false`, the UI prompts for external skills source (Claude/ChatGPT/None). Experiences always export from CHL DB.
-   **Import from Google Sheets:** Click **Import Spreadsheet** to overwrite the local database with the published sheet (reset all).
    - If `CHL_SKILLS_ENABLED=false`, experiences import into CHL DB and skills are written to external SKILLS.md folders based on the modal choice.

## 5.3. Demo Run (DataPipe Sample)

This demo shows how CHL teaches LLMs project-specific conventions using a fictional "DataPipe" project. It highlights the difference between generic bug reporting vs. team-specific ticket requirements.

### What You'll See

**Without CHL:**
- LLM rushes to fix code when user reports a bug.
- LLM writes incomplete tickets missing required artifacts (Run ID, pipeline stage, logs).

**With CHL:**
- LLM clarifies user intent first (fix vs. document vs. investigate).
- LLM enforces project-specific ticket requirements.
- LLM asks for required artifacts before drafting tickets.

**The demo takes ~10 minutes end-to-end.**

### Prerequisites

Before running the demo, complete the main installation steps in the README:
1. Install API server (CPU or GPU mode)
2. Configure environment (.env file)
3. Initialize database
4. Start API server
5. Install MCP server

The demo script `scripts/setup/demo_datapipe_bug.py` is included in the repository and ready to run.

### Sample Data

The demo requires TMG (Ticket Management) category data with DataPipe bug reporting guidance. This data is automatically seeded when you run `python scripts/setup/setup-cpu.py` or `python scripts/setup/setup-gpu.py`.

Verify the data is present:
1. Set `IMPORT_SPREADSHEET_ID` in your `.env` (same as `.env.sample`).
2. Import your database via Settings → "Import Spreadsheet" (this resets local data).
3. Check the Experiences worksheet for TMG entries about bug reporting.
4. Check the Skills worksheet for the "Bug Report Template" entry.

### Running the Demo

#### Step 1: Generate Bug Artifacts

```bash
# Activate your API server venv first
source .venv-cpu/bin/activate  # Or .venv-apple / .venv-nvidia

# Run the buggy script
python3 scripts/setup/demo_datapipe_bug.py
```

The script will:
- Fail with a realistic error (missing data file).
- Print the error message to console.
- Save artifacts to:
  - `data/output/run_meta.json` (Run ID, pipeline stage, timestamp)
  - `data/output/app.log` (error details and stack trace)

Copy the error message from the console; you'll paste this into your AI assistant.

#### Step 2: Test A - Without CHL (Baseline)

Setup:
- Start a fresh chat session in your code assistant.
- Do NOT mention CHL or use any MCP tools.
- Or temporarily disable CHL MCP server and restart your assistant.

Test sequence:
1. **Pitfall #1: Rushing to code**
   ```
   You: "I found a bug in DataPipe, here's the error: [paste error]"
   ```
   Expected: LLM immediately starts reading code files and attempting fixes without asking what you want.
2. **Pitfall #2: Incomplete ticket**
   ```
   You: "Actually, don't fix it. Write a bug ticket for this error instead."
   ```
   Expected: LLM writes a generic ticket missing Run ID, pipeline stage, and log excerpt.

#### Step 3: Test B - With CHL (Improved)

Setup:
- Ensure CHL MCP server is running (`./start-chl.sh`).
- Ensure TMG sample data is imported (verify via export).
- Start a fresh chat session in your code assistant (with CHL enabled).

Test sequence:
1. Configure agent instructions:
   - **For Claude Code**: Copy or append `CLAUDE.md.sample` to `<project root>/.claude/CLAUDE.md`. This file will be automatically loaded at session startup.
   - **For other code assistants** (Codex, Cursor, Windsurf, etc.): Follow the instructions in `AGENTS.md.sample` to add the content to your assistant's custom instructions or configuration
2. **Fix #1: Clarifies intent first**
   ```
   You: "I found a bug in DataPipe, here's the error: [paste same error]"
   ```
   Expected: LLM calls `read_entries(...)` and asks whether to fix, document, or investigate.
3. **Fix #2: Enforces project requirements**
   ```
   You: "Write a bug ticket."
   ```
   Expected: LLM requests required artifacts, reads the JSON/log files, and writes a complete ticket.

### Understanding the Value

- **Intent Clarification**: CHL teaches the LLM to pause and clarify before acting.
- **Process Enforcement**: CHL stores your team's bug reporting conventions.
- **Artifact Awareness**: CHL guides the LLM to use project-specific artifacts (Run ID, logs, metadata).

### Next Steps

1. Add your own categories: update taxonomy for your team's workflows.
2. Work with the LLM normally; it will pull guidance as you go.
3. Close the loop at conversation end: summarize and capture new experiences/skills.
4. Share when it's ripe: export to Google Sheets to share with the team.
5. Keep refining: repeat the loop to keep the KB sharp and relevant.

## 6. Reference

### 6.1. Category Index
Categories are defined in code and seeded during setup. See the canonical list in `src/common/config/categories.py`.

### 6.2. Managing Categories

Categories define the organizational "shelves" where experiences and skills are stored. Categories are defined in code and validated on import; any CSV categories are ignored.

#### Adding New Categories

1. Update `src/common/config/categories.py` with the new category (code/name/description).
2. Commit and share the change with the team.
3. Run setup or import to seed the updated taxonomy into local databases.
4. Verify via MCP `list_categories` or `GET /api/v1/categories/`.

#### Important Notes

- **Import is destructive**: The import operation replaces local experience/skill data (categories are re-seeded from code).
- **Category naming**: Use short, memorable codes (3-4 uppercase letters) and descriptive snake_case names.
- **Team coordination**: Treat taxonomy changes like code changes (review + merge).

#### Category Best Practices

- **Be specific**: Create categories for distinct workflow types.
- **Avoid overlap**: Don't create multiple categories that could store the same type of knowledge.
- **Document purpose**: Use clear descriptions so team members know what each category is for.

### 6.3. Environment Variables
While `scripts/scripts_config.yaml` is preferred, the scripts and server can be configured with environment variables. Key variables include:

- `CHL_EXPERIENCE_ROOT` - Path to data directory
- `CHL_DATABASE_PATH` - Path to SQLite database file
- `CHL_EMBEDDING_REPO` - Embedding model repository (GPU mode only)
- `CHL_EMBEDDING_N_GPU_LAYERS` / `CHL_RERANKER_N_GPU_LAYERS` - Optional GPU offload depth for GGUF models (`0` = CPU-only, `-1` = all layers, `N` = first N layers). Works with Metal, CUDA, and ROCm wheels.
- `GOOGLE_CREDENTIAL_PATH` - Service account JSON for Sheets access
- `EXPORT_SPREADSHEET_ID` - Google Sheets ID for review/export
- `IMPORT_SPREADSHEET_ID` - Google Sheets ID for published/import

**Note:** The backend (cpu/metal/nvidia/amd) is automatically determined from `data/runtime_config.json` (created by `scripts/setup/check_api_env.py`). No manual configuration needed.

For a complete list of configuration options, see [src/common/config/config.py](../src/common/config/config.py).

## 7. CPU-Only Mode

CHL can run in CPU-only mode without ML dependencies (FAISS, embeddings, reranker) using SQLite text search instead of semantic search.

### 7.1. When to Use CPU-Only Mode

Use CPU-only mode when:

- You don't have sufficient GPU VRAM (≥8 GB recommended for GPU mode)
- You don't need semantic search and keyword matching is sufficient
- You want to minimize dependencies and resource usage
- You're running on constrained hardware or in containers

### 7.2. Installation

Install CHL without ML extras (API server venv):
```bash
# Create and activate the API server venv
python3 -m venv .venv-cpu
source .venv-cpu/bin/activate  # Windows: .venv-cpu\Scripts\activate

# Install API dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements_cpu.txt
```

Run diagnostics to configure CPU mode:
```bash
python scripts/setup/check_api_env.py
# Select option 1 for CPU-only mode
# This creates data/runtime_config.json with backend="cpu"
```

Run setup (no ML model downloads):
```bash
python scripts/setup/setup-cpu.py
```

Start the server:
```bash
python -m uvicorn src.api.server:app --host 127.0.0.1 --port 8000
# Backend is automatically detected from data/runtime_config.json
```

### 7.3. Behavior Differences

In CPU-only mode:

- **Search**: Uses SQLite `LIKE` queries for keyword matching instead of semantic similarity
- **Duplicate detection**: Uses simple text matching instead of embedding-based similarity
- **Background worker**: No embedding worker runs; entries are immediately available for search
- **Vector components**: FAISS, embedding models, and reranker are not initialized
- **Imports**: Import operations skip worker coordination because `/admin/queue/*` endpoints are unavailable without the embedding worker

### 7.4. Search Tips for CPU-Only Mode

Since SQLite text search uses literal keyword matching:

- Use specific keywords from entry titles and content
- Search for exact phrases when possible
- Break complex queries into multiple searches
- Use category filtering to narrow results
- Avoid abstract or conceptual queries (e.g., "best practices" won't match "recommended approaches")

### 7.5. Switching Modes

To switch between CPU and GPU modes:
1. Stop the API server
2. Run `python scripts/setup/check_api_env.py` and select target mode
3. Create a new venv and install the corresponding requirements file
4. GPU mode only: run `python scripts/setup/setup-gpu.py --download-models`
5. Start the API server (uses `data/runtime_config.json`)

### 7.6. Limitations

CPU-only mode has the following limitations:

  - No semantic search or conceptual matching
  - No embedding-based duplicate detection
  - No reranking of search results
  - Search quality depends on exact keyword matches
  - Cannot import FAISS snapshots from GPU instances

For teams that need semantic search, consider running one GPU instance to build FAISS snapshots, but note that CPU-only instances cannot load these snapshots.
