# CHL Operator's Manual

This manual covers the setup, daily workflows, and operational tasks for the Curated Heuristic Loop (CHL) system. For the project's philosophy, see [concept.md](./concept.md), and for technical details, see [architecture.md](./architecture.md).

## 1. Initial Setup

This section guides you through the first-time setup of the CHL environment.

### 1.1. Quick Start
For the fastest setup, please follow the **Quick Start** guide in the main [README.md](../README.md). It will guide you through installing dependencies and starting the web server. The rest of this manual assumes you have completed those steps.

### 1.2. First-Time Setup Script
The `setup.py` script initializes your local environment.

**Command:**
```bash
uv run python scripts/setup.py
```

**When to use:**
- After first cloning the repository.
- To re-download models after changing the selection (`CHL_EMBEDDING_REPO`).
- If your `data/` directory is deleted or corrupted.

**What it does:**
1. Creates the `data/` directory structure.
2. Initializes the SQLite database (`chl.db`).
3. Downloads the required embedding and reranker models.
4. Creates the FAISS index directory.

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

The simplest way to manage CHL is through the built-in web interface, available by running `uvicorn src.api_server:app` and navigating to `http://127.0.0.1:8000`.

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
While `scripts/scripts_config.yaml` is preferred, the scripts and server can be configured with environment variables. See the old `manual.md` for a complete list if needed. Key variables include:
- `CHL_EXPERIENCE_ROOT`
- `CHL_DATABASE_PATH`
- `CHL_EMBEDDING_REPO`
- `CHL_REVIEW_SHEET_ID`
- `CHL_PUBLISHED_SHEET_ID`

## 7. Troubleshooting

-   **Script won't run:** Ensure you are in the project root and using the `uv` environment (`uv run ...`).
-   **Import errors:** Your dependencies may be out of sync. Run `uv sync --python 3.11 --extra ml`.
-   **Permission denied:** Make scripts executable with `chmod +x scripts/<script_name>.py`.

## 8. Script Development Guidelines

Follow the structure in `scripts/_template.py` when adding new scripts. Ensure they use `src.config.get_config()` and have clear documentation.